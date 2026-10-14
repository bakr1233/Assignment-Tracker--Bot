/**
 * Assignment tracker - Google Sheet receiver.
 *
 * Paste this whole file into Extensions -> Apps Script on your tracker sheet,
 * put your shared secret in SHARED_SECRET below, then deploy it as a Web app
 * (Execute as: Me, Who has access: Anyone) and send the /exec URL to the bot.
 *
 * It keeps two tabs:
 *   "Bot Data"     every assignment found, hidden. The store.
 *   the tracker    your Assignment Tracker layout, rebuilt each run, showing
 *                  only what is due within the next few days. Work further out
 *                  waits in Bot Data and arrives on its own.
 *
 * ADOPTING AN EXISTING TAB
 * Normally the script refuses to write to a tab it did not create, because an
 * early version scrambled a hand-made tracker by adopting one and sorting it.
 * When the bot is deliberately pointed at your own tab it sends adopt:true,
 * and the script then takes it over - but it copies the tab to a dated backup
 * first, so nothing you had is lost.
 *
 * WHAT IT WRITES
 * Subject, Assignment and Due On are values. Days Left stays the formula from
 * the original tracker, so it keeps counting down between runs and still shows
 * "Done" and "Skipped" the way it always did. Due On is written as =DATE(...)
 * so no time zone can shift it by a day.
 *
 * Status is yours. The bot seeds it and records what it wrote in a hidden
 * column; once the two disagree it never touches that cell again.
 */

var SHARED_SECRET = 'PASTE_THE_SECRET_FROM_config.json_HERE';

/* ---------------------------------------------------------------- the store */

var DATA_HEADERS = [
  'Key', 'Sort', 'Course', 'Subject', 'Assignment', 'Due', 'Due On',
  'Days left', 'Bot status', 'Points', 'Source', 'Link', 'First seen', 'Last seen',
  'Done at'
];

var D = {
  key: 1, sort: 2, course: 3, subject: 4, assignment: 5, due: 6, dueOn: 7,
  daysLeft: 8, botStatus: 9, points: 10, source: 11, link: 12,
  firstSeen: 13, lastSeen: 14, doneAt: 15
};

/** Header cells that prove a tab belongs to this script, as [index, text].
 *  Named explicitly rather than "the last two" so columns can be added to the
 *  end without silently changing what ownership means. */
var DATA_MARKERS = [[0, 'Key'], [1, 'Sort']];

var MEANINGFUL = [D.course, D.assignment, D.due, D.botStatus, D.points, D.source, D.link];
var NO_DUE = '9999-12-31';

/* -------------------------------------------------------------- the tracker */

/** A-E are exactly the original layout. F and G are the bot's plumbing,
 *  hidden, so the visible design is unchanged. */
var VIEW_HEADERS = ['Subject', 'Assignment', 'Status', 'Days Left', 'Due On', 'Key', 'Written'];

var V = { subject: 1, assignment: 2, status: 3, daysLeft: 4, dueOn: 5, key: 6, written: 7 };
var VIEW_HIDDEN = [V.key, V.written];

/** Columns F and G. A tracker built by hand shares the visible headers but
 *  never these, which is the whole point of checking them and not Subject. */
var VIEW_MARKERS = [[V.key - 1, 'Key'], [V.written - 1, 'Written']];

var TITLE_ROW = 1;
var LEGEND_ROW = 3;
var HEAD_ROW = 4;
var FIRST_DATA_ROW = 5;

var NAVY = '#1f3864';
var LEGEND_BG = '#eef2f8';
var ROW_BG = '#ffffff';
var ROW_ALT = '#f4f7fb';
var RED_BG = '#f4c7c3';
var RED_INK = '#9c0006';
var BLUE_BG = '#cfe2f3';
var BLUE_INK = '#1155cc';
var DONE_BG = '#d9ead3';
var DONE_INK = '#38761d';
var GREY_INK = '#8a8a8a';

var STATUS_CHOICES = ['Not started', 'In progress', 'Done', 'Skipped', 'Exam/Quiz'];
var DEFAULT_STATUS = 'Not started';

var LEGEND =
  'Days Left key:  Dark red = Overdue / Today / 0-1 days  ·  ' +
  'Light red = 2-7 days  ·  Blue = 8-14 days';

/* ------------------------------------------------------------------ routing */

function doGet() {
  return ContentService
    .createTextOutput(
      'Assignment tracker receiver is deployed. The bot posts to this URL; ' +
      'there is nothing to see in a browser.'
    )
    .setMimeType(ContentService.MimeType.TEXT);
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (err) {
    return reply({ ok: false, error: 'Another run is still writing. Try again.' });
  }

  try {
    if (!e || !e.postData || !e.postData.contents) {
      return reply({ ok: false, error: 'Empty request.' });
    }

    var body = JSON.parse(e.postData.contents);

    if (SHARED_SECRET === 'PASTE_THE_SECRET_FROM_config.json_HERE') {
      return reply({ ok: false, error: 'SHARED_SECRET has not been set in the script yet.' });
    }
    if (body.secret !== SHARED_SECRET) {
      return reply({ ok: false, error: 'Wrong shared secret.' });
    }

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var viewName = body.tab || 'Bot Assignments';
    var dataName = body.data_tab || 'Bot Data';

    if (body.action === 'ping') {
      return reply({ ok: true, spreadsheet: ss.getName(), tab: viewName, dataTab: dataName });
    }
    if (body.action !== 'upsert') {
      return reply({ ok: false, error: 'Unknown action: ' + body.action });
    }

    return reply(upsert(ss, viewName, dataName, body));
  } catch (err) {
    return reply({ ok: false, error: String(err && err.message ? err.message : err) });
  } finally {
    lock.releaseLock();
  }
}

/* ------------------------------------------------------------------- owning */

function ownedTab(ss, tabName, headerRow, headers, markers, adopt, notes) {
  var sheet = ss.getSheetByName(tabName);

  if (!sheet) {
    sheet = ss.insertSheet(tabName);
    sheet.getRange(headerRow, 1, 1, headers.length).setValues([headers]);
    return sheet;
  }

  if (sheet.getLastRow() === 0) {
    sheet.getRange(headerRow, 1, 1, headers.length).setValues([headers]);
    return sheet;
  }

  if (isOurs(sheet, headerRow, headers, markers)) {
    // Rewrite the header row so a column added in a later version appears.
    sheet.getRange(headerRow, 1, 1, headers.length).setValues([headers]);
    return sheet;
  }

  // A tab written by an older version of this script: 'Key' in A1.
  if (headerRow !== 1 && String(sheet.getRange(1, 1).getValue()).trim() === 'Key') {
    resetSheet(sheet, headerRow, headers);
    return sheet;
  }

  if (adopt) {
    // Copy it first. Taking over a tab somebody built by hand is only
    // acceptable if the original is still there to go back to.
    var backup = tabName + ' (backup ' + stampName() + ')';
    try {
      sheet.copyTo(ss).setName(backup);
      notes.push('Backed the old tab up as "' + backup + '".');
    } catch (err) {
      throw new Error(
        'Could not back up "' + tabName + '" before taking it over, so nothing ' +
        'was written. ' + (err && err.message ? err.message : err)
      );
    }
    resetSheet(sheet, headerRow, headers);
    return sheet;
  }

  throw new Error(
    'The tab "' + tabName + '" already contains something else, so nothing was ' +
    'written - the bot only touches a tab it created itself. Either point ' +
    '"sheet_tab" in config.json at a free name, or set "adopt_existing_tab" ' +
    'to true to let it take that tab over (it will back it up first).'
  );
}

/**
 * Ownership is decided on marker columns, never on the visible headers.
 *
 * The visible headers are copied from the original tracker - Subject,
 * Assignment, Status - so a hand-made tab matches them exactly. Checking those
 * made the script mistake somebody's own tracker for one of its own: it then
 * skipped the backup and overwrote the tab. Markers are the hidden plumbing
 * columns, which appear nowhere else. Do not "simplify" this to the first two
 * headers; that bug has cost a real spreadsheet once already.
 */
function isOurs(sheet, headerRow, headers, markers) {
  if (sheet.getMaxRows() < headerRow) {
    return false;
  }
  var existing = sheet.getRange(headerRow, 1, 1, headers.length).getValues()[0];
  for (var i = 0; i < markers.length; i++) {
    if (String(existing[markers[i][0]]).trim() !== markers[i][1]) {
      return false;
    }
  }
  return true;
}

function resetSheet(sheet, headerRow, headers) {
  sheet.clear();
  try {
    sheet.setConditionalFormatRules([]);
  } catch (err) {
    // Not every container exposes this; clear() covers the rest.
  }
  try {
    sheet.getRange(1, 1, sheet.getMaxRows(), sheet.getMaxColumns()).clearDataValidations();
  } catch (err) {
    // Same.
  }
  sheet.getRange(headerRow, 1, 1, headers.length).setValues([headers]);
}

function stampName() {
  var now = new Date();
  function two(n) { return (n < 10 ? '0' : '') + n; }
  return now.getFullYear() + '-' + two(now.getMonth() + 1) + '-' + two(now.getDate()) +
    ' ' + two(now.getHours()) + two(now.getMinutes());
}

/* ------------------------------------------------------------------ writing */

function upsert(ss, viewName, dataName, body) {
  var items = body.items || [];
  var withinDays = body.display_within_days === undefined ? 7 : Number(body.display_within_days);
  var graceDays = body.overdue_grace_days === undefined ? 1 : Number(body.overdue_grace_days);
  var notes = [];

  var lingerMs = (body.done_linger_minutes === undefined ? 30 : Number(body.done_linger_minutes))
    * 60 * 1000;

  var dataSheet = ownedTab(ss, dataName, 1, DATA_HEADERS, DATA_MARKERS, false, notes);
  var viewSheet = ownedTab(ss, viewName, HEAD_ROW, VIEW_HEADERS, VIEW_MARKERS, !!body.adopt, notes);

  var yours = readYourStatuses(viewSheet);
  var result = writeStore(dataSheet, items, yours);
  var shown = buildView(
    viewSheet, dataSheet, yours, withinDays, graceDays, lingerMs, body.term || ''
  );

  result.tab = viewName;
  result.dataTab = dataName;
  result.shown = shown;
  result.hidden = result.rows - shown;
  if (notes.length) {
    result.notes = notes;
  }
  return result;
}

function writeStore(sheet, items, yours) {
  var stamp = new Date();
  var width = Math.max(sheet.getLastColumn(), DATA_HEADERS.length);
  var lastRow = sheet.getLastRow();
  var block = lastRow > 1 ? sheet.getRange(2, 1, lastRow - 1, width).getValues() : [];

  var indexByKey = {};
  for (var i = 0; i < block.length; i++) {
    var existingKey = String(block[i][D.key - 1] || '').trim();
    if (existingKey) {
      indexByKey[existingKey] = i;
    }
  }

  var added = 0, updated = 0, unchanged = 0;

  for (var n = 0; n < items.length; n++) {
    var item = items[n];
    if (!item || !item.key) {
      continue;
    }
    var at = indexByKey[item.key];
    if (at === undefined) {
      block.push(storeRow(blankRow(width), item, stamp));
      indexByKey[item.key] = block.length - 1;
      added++;
    } else {
      var before = block[at].slice();
      block[at] = storeRow(before, item, stamp);
      if (rowChanged(before, block[at])) {
        updated++;
      } else {
        unchanged++;
      }
    }
  }

  // Start or clear each row's "done" clock. Doing it here, after the merge,
  // means it sees both what the sources reported and what you typed.
  for (var r = 0; r < block.length; r++) {
    var rowKey = String(block[r][D.key - 1] || '').trim();
    var effective = (yours && yours[rowKey]) || block[r][D.botStatus - 1];
    if (isDone(effective)) {
      if (!asDate(block[r][D.doneAt - 1])) {
        block[r][D.doneAt - 1] = stamp;
      }
    } else {
      // Changed your mind - the countdown starts again if you mark it later.
      block[r][D.doneAt - 1] = '';
    }
  }

  // Sheets would parse "Sun 18 Oct, 11:59 PM" into a date value, so what came
  // back would never equal what was sent and every row would look changed.
  forceText(sheet, [D.key, D.sort, D.due, D.dueOn, D.points]);

  if (block.length) {
    block.sort(function (a, b) {
      var left = String(a[D.sort - 1] || NO_DUE);
      var right = String(b[D.sort - 1] || NO_DUE);
      return left === right ? 0 : (left < right ? -1 : 1);
    });
    sheet.getRange(2, 1, block.length, width).setValues(block);
  }

  sheet.getRange(1, 1, 1, DATA_HEADERS.length)
    .setValues([DATA_HEADERS]).setFontWeight('bold').setBackground('#efefef');
  sheet.setFrozenRows(1);
  try {
    sheet.hideSheet();
  } catch (err) {
    // Refused when it is the only visible tab.
  }

  return {
    ok: true, added: added, updated: updated, unchanged: unchanged,
    received: items.length, rows: block.length
  };
}

function storeRow(before, item, stamp) {
  var row = before.slice();
  row[D.key - 1] = item.key;
  row[D.sort - 1] = item.sort || NO_DUE;
  row[D.course - 1] = pick(item.course, before[D.course - 1]);
  row[D.subject - 1] = pick(item.subject, before[D.subject - 1]);
  row[D.assignment - 1] = pick(item.title, before[D.assignment - 1]);
  row[D.due - 1] = pick(item.due, before[D.due - 1]);
  row[D.dueOn - 1] = pick(item.due_on, before[D.dueOn - 1]);
  row[D.daysLeft - 1] = (item.days_left === undefined || item.days_left === null)
    ? '' : item.days_left;
  row[D.botStatus - 1] = item.done ? 'Done' : pick(item.status, before[D.botStatus - 1]);
  row[D.points - 1] = pick(item.points, before[D.points - 1]);
  row[D.source - 1] = pick(item.source, before[D.source - 1]);
  row[D.link - 1] = pick(item.url, before[D.link - 1]);
  row[D.firstSeen - 1] = before[D.firstSeen - 1] || stamp;
  row[D.lastSeen - 1] = stamp;
  return row;
}

/* ----------------------------------------------------------------- the view */

function readYourStatuses(sheet) {
  var yours = {};
  var lastRow = sheet.getLastRow();
  if (lastRow < FIRST_DATA_ROW) {
    return yours;
  }

  var rows = sheet
    .getRange(FIRST_DATA_ROW, 1, lastRow - FIRST_DATA_ROW + 1, VIEW_HEADERS.length)
    .getValues();

  for (var i = 0; i < rows.length; i++) {
    var key = String(rows[i][V.key - 1] || '').trim();
    if (!key) {
      continue;
    }
    var shown = String(rows[i][V.status - 1] || '').trim();
    var written = String(rows[i][V.written - 1] || '').trim();
    if (shown && shown !== written) {
      yours[key] = shown;
    }
  }
  return yours;
}

function isDone(status) {
  return String(status || '').trim().toLowerCase() === 'done';
}

/** A usable date, or null. Duck-typed rather than `instanceof Date` so it
 *  cannot be fooled by a value that arrived from somewhere else. */
function asDate(value) {
  if (value && typeof value.getTime === 'function') {
    var ms = value.getTime();
    if (!isNaN(ms)) {
      return value;
    }
  }
  return null;
}

function buildView(sheet, dataSheet, yours, withinDays, graceDays, lingerMs, term) {
  var now = new Date();
  var lastRow = dataSheet.getLastRow();
  var store = lastRow > 1
    ? dataSheet.getRange(2, 1, lastRow - 1, DATA_HEADERS.length).getValues()
    : [];

  var rows = [];
  for (var i = 0; i < store.length; i++) {
    var left = store[i][D.daysLeft - 1];
    if (left === '' || left === null) {
      continue;
    }
    if (Number(left) > withinDays) {
      continue;   // waiting its turn in the store
    }
    if (Number(left) < -graceDays) {
      continue;   // missed long enough ago to stop cluttering the tracker
    }

    var key = String(store[i][D.key - 1] || '').trim();
    if (!key) {
      continue;
    }

    var botStatus = String(store[i][D.botStatus - 1] || '').trim();
    if (STATUS_CHOICES.indexOf(botStatus) === -1) {
      botStatus = botStatus.toLowerCase() === 'done' ? 'Done' : DEFAULT_STATUS;
    }

    // Finished work stays up briefly so you can see it tick over, then goes.
    var status = yours[key] || botStatus;
    if (isDone(status)) {
      var doneAt = asDate(store[i][D.doneAt - 1]);
      if (doneAt && now.getTime() - doneAt.getTime() >= lingerMs) {
        continue;
      }
    }

    var line = FIRST_DATA_ROW + rows.length;
    rows.push([
      store[i][D.subject - 1],
      store[i][D.assignment - 1],
      status,
      daysLeftFormula(line),
      dateFormula(store[i][D.sort - 1]),
      key,
      botStatus
    ]);
  }

  paintView(sheet, rows, term);
  return rows.length;
}

/** The original tracker's own formula, kept rather than replaced with a
 *  number: it keeps counting down between runs, and still shows "Done" and
 *  "Skipped" from the Status column the way it always did. */
function daysLeftFormula(row) {
  return '=IF(E' + row + '="","",IF(C' + row + '="Done","Done",IF(C' + row + '="Skipped","—",' +
    'IF(E' + row + '<TODAY(),"Overdue",IF(E' + row + '=TODAY(),"Today",E' + row + '-TODAY())))))';
}

/** =DATE(y,m,d) rather than a Date object, so no time zone can shift it. */
function dateFormula(sortIso) {
  var found = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(sortIso || ''));
  if (!found || found[1] === '9999') {
    return '';
  }
  return '=DATE(' + Number(found[1]) + ',' + Number(found[2]) + ',' + Number(found[3]) + ')';
}

function paintView(sheet, rows, term) {
  var width = VIEW_HEADERS.length;
  var maxRows = sheet.getMaxRows();

  if (maxRows >= FIRST_DATA_ROW) {
    var body = sheet.getRange(FIRST_DATA_ROW, 1, maxRows - FIRST_DATA_ROW + 1, width);
    body.clearContent();
    body.clearDataValidations();
    body.setBackground(ROW_BG).setFontColor('#000000')
      .setFontWeight('normal').setFontLine('none');
  }

  // Title.
  sheet.getRange(TITLE_ROW, 1, 1, width).setBackground(NAVY);
  sheet.getRange(TITLE_ROW, V.subject).setValue('Assignment Tracker')
    .setFontSize(24).setFontWeight('bold').setFontColor('#ffffff')
    .setVerticalAlignment('middle');
  sheet.getRange(TITLE_ROW, V.dueOn).setValue(term)
    .setFontSize(16).setFontWeight('bold').setFontColor('#c6d2e8')
    .setHorizontalAlignment('right').setVerticalAlignment('middle');
  sheet.setRowHeight(TITLE_ROW, 52);

  sheet.getRange(2, 1, 1, width).setBackground(NAVY);
  sheet.setRowHeight(2, 5);

  // Legend.
  sheet.getRange(LEGEND_ROW, 1, 1, width).setBackground(LEGEND_BG);
  sheet.getRange(LEGEND_ROW, V.assignment).setValue(LEGEND)
    .setFontStyle('italic').setFontColor('#44546a').setHorizontalAlignment('center');
  sheet.setRowHeight(LEGEND_ROW, 26);

  // Header.
  sheet.getRange(HEAD_ROW, 1, 1, width).setValues([VIEW_HEADERS])
    .setBackground(NAVY).setFontColor('#ffffff').setFontWeight('bold')
    .setHorizontalAlignment('center').setVerticalAlignment('middle');
  sheet.setRowHeight(HEAD_ROW, 32);
  sheet.setFrozenRows(HEAD_ROW);

  if (rows.length) {
    var target = sheet.getRange(FIRST_DATA_ROW, 1, rows.length, width);
    target.setValues(rows);
    target.setVerticalAlignment('middle');

    for (var i = 0; i < rows.length; i++) {
      sheet.getRange(FIRST_DATA_ROW + i, 1, 1, width)
        .setBackground(i % 2 === 0 ? ROW_BG : ROW_ALT);
    }

    sheet.getRange(FIRST_DATA_ROW, V.subject, rows.length, 1).setHorizontalAlignment('center');
    sheet.getRange(FIRST_DATA_ROW, V.status, rows.length, 1).setHorizontalAlignment('center');
    sheet.getRange(FIRST_DATA_ROW, V.daysLeft, rows.length, 1)
      .setHorizontalAlignment('center').setFontWeight('bold');
    sheet.getRange(FIRST_DATA_ROW, V.dueOn, rows.length, 1)
      .setHorizontalAlignment('center').setFontWeight('bold')
      .setNumberFormat('dddd, m/d');

    addStatusDropdown(sheet, rows.length);
    applyColours(sheet, rows.length);
  } else {
    try {
      sheet.setConditionalFormatRules([]);
    } catch (err) {
      // Nothing to clear.
    }
  }

  for (var h = 0; h < VIEW_HIDDEN.length; h++) {
    sheet.hideColumns(VIEW_HIDDEN[h]);
  }
  setWidth(sheet, V.subject, 120);
  setWidth(sheet, V.assignment, 420);
  setWidth(sheet, V.status, 140);
  setWidth(sheet, V.daysLeft, 120);
  setWidth(sheet, V.dueOn, 190);

  try {
    sheet.setTabColor(NAVY);
  } catch (err) {
    // Decoration only.
  }
}

/** Conditional rules rather than fixed fills, because Days Left is a formula
 *  that keeps changing on its own between runs. */
function applyColours(sheet, count) {
  var body = sheet.getRange(FIRST_DATA_ROW, 1, count, 5);
  var days = sheet.getRange(FIRST_DATA_ROW, V.daysLeft, count, 1);
  var d = '$D' + FIRST_DATA_ROW;
  var c = '$C' + FIRST_DATA_ROW;

  var rules = [
    // Finished work greys out and is struck through, across the whole row.
    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=' + c + '="Done"')
      .setFontColor(GREY_INK).setStrikethrough(true)
      .setRanges([body]).build(),

    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=' + d + '="Done"')
      .setBackground(DONE_BG).setFontColor(DONE_INK)
      .setRanges([days]).build(),

    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=OR(' + d + '="Overdue",' + d + '="Today")')
      .setBackground(RED_BG).setFontColor(RED_INK).setBold(true)
      .setRanges([days]).build(),

    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND(ISNUMBER(' + d + '),' + d + '<=7)')
      .setBackground(RED_BG).setFontColor(RED_INK).setBold(true)
      .setRanges([days]).build(),

    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND(ISNUMBER(' + d + '),' + d + '>=8,' + d + '<=14)')
      .setBackground(BLUE_BG).setFontColor(BLUE_INK).setBold(true)
      .setRanges([days]).build()
  ];

  sheet.setConditionalFormatRules(rules);
}

function addStatusDropdown(sheet, count) {
  try {
    var rule = SpreadsheetApp.newDataValidation()
      .requireValueInList(STATUS_CHOICES, true).setAllowInvalid(true).build();
    sheet.getRange(FIRST_DATA_ROW, V.status, count, 1).setDataValidation(rule);
  } catch (err) {
    // Dropdowns are a convenience.
  }
}

/* ------------------------------------------------------------------ helpers */

function forceText(sheet, columns) {
  var rows = Math.max(sheet.getMaxRows() - 1, 1);
  for (var i = 0; i < columns.length; i++) {
    try {
      sheet.getRange(2, columns[i], rows, 1).setNumberFormat('@');
    } catch (err) {
      // Best effort.
    }
  }
}

function blankRow(width) {
  var row = [];
  for (var i = 0; i < width; i++) {
    row.push('');
  }
  return row;
}

function pick(incoming, existing) {
  return (incoming === undefined || incoming === null || incoming === '') ? existing : incoming;
}

function rowChanged(before, after) {
  for (var i = 0; i < MEANINGFUL.length; i++) {
    var c = MEANINGFUL[i] - 1;
    if (String(before[c]) !== String(after[c])) {
      return true;
    }
  }
  return false;
}

function setWidth(sheet, column, width) {
  try {
    sheet.setColumnWidth(column, width);
  } catch (err) {
    // Cosmetic.
  }
}

function reply(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
