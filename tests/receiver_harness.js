/**
 * Runs sheet_receiver.gs against a stand-in for Google Sheets.
 *
 *   node tests/receiver_harness.js
 *
 * The checks that matter most:
 *   - it refuses a tab it does not own unless explicitly told to adopt one,
 *     and when it does adopt, it backs the tab up first;
 *   - only work due within the window reaches the tracker, the rest waits;
 *   - Days Left stays the original tracker's formula, not a fixed number;
 *   - a status you typed is never overwritten by a later run.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// Sheets turns a date-like string into a real date value unless the cell is
// formatted as text first. That coercion once made every row look "changed".
const DATE_LIKE = /^[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}, \d{1,2}:\d{2} (AM|PM)$/;

function makeSheet(name, initialRows, register) {
  const cells = (initialRows || []).map((row) => row.slice());
  const textColumns = new Set();
  let frozen = 0;
  let hidden = false;
  let rules = [];

  const coerce = (column, value) => {
    if (typeof value === 'string' && DATE_LIKE.test(value) && !textColumns.has(column)) {
      return new Date('2026-10-06T23:59:00');
    }
    return value;
  };

  const get = (r, c) =>
    cells[r - 1] && cells[r - 1][c - 1] !== undefined ? cells[r - 1][c - 1] : '';

  const set = (r, c, v) => {
    while (cells.length < r) cells.push([]);
    const row = cells[r - 1];
    while (row.length < c) row.push('');
    row[c - 1] = v;
  };

  const lastRow = () => {
    let last = 0;
    cells.forEach((row, i) => {
      if (row.some((v) => v !== '' && v !== undefined)) last = i + 1;
    });
    return last;
  };

  const lastCol = () => {
    let last = 0;
    cells.forEach((row) => {
      row.forEach((v, j) => {
        if (v !== '' && v !== undefined && j + 1 > last) last = j + 1;
      });
    });
    return last;
  };

  function makeRange(r, c, nr, nc) {
    const noop = () => range;
    const range = {
      getValue: () => get(r, c),
      setValue: (v) => {
        set(r, c, coerce(c, v));
        return range;
      },
      getValues: () => {
        const out = [];
        for (let i = 0; i < nr; i++) {
          const row = [];
          for (let j = 0; j < nc; j++) row.push(get(r + i, c + j));
          out.push(row);
        }
        return out;
      },
      setValues: (vals) => {
        vals.forEach((row, i) => row.forEach((v, j) => set(r + i, c + j, coerce(c + j, v))));
        return range;
      },
      clearContent: () => {
        for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++) set(r + i, c + j, '');
        return range;
      },
      setNumberFormat: (fmt) => {
        if (fmt === '@') for (let j = 0; j < nc; j++) textColumns.add(c + j);
        return range;
      },
      clearDataValidations: noop,
      setDataValidation: noop,
      setFontWeight: noop,
      setFontColor: noop,
      setFontSize: noop,
      setFontStyle: noop,
      setFontLine: noop,
      setBackground: noop,
      setVerticalAlignment: noop,
      setHorizontalAlignment: noop,
    };
    return range;
  }

  const sheet = {
    getName: () => name,
    setName: (n) => {
      name = n;
      if (register) register(n, sheet);
      return sheet;
    },
    getLastRow: lastRow,
    getLastColumn: lastCol,
    getMaxRows: () => Math.max(cells.length, 200),
    getMaxColumns: () => 12,
    getRange: (r, c, nr, nc) =>
      makeRange(r, c, nr === undefined ? 1 : nr, nc === undefined ? 1 : nc),
    setFrozenRows: (n) => {
      frozen = n;
    },
    getFrozenRows: () => frozen,
    setRowHeight: () => {},
    hideColumns: () => {},
    setColumnWidth: () => {},
    setTabColor: () => {},
    copyTo: () => makeSheet(name + ' (copy)', cells, register),
    clear: () => {
      cells.length = 0;
    },
    getConditionalFormatRules: () => rules,
    setConditionalFormatRules: (r) => {
      rules = r;
    },
    hideSheet: () => {
      hidden = true;
    },
    isHidden: () => hidden,
    _dump: () => cells.map((row) => row.slice()),
  };

  return sheet;
}

function makeSandbox() {
  const sheets = {};
  const register = (n, s) => {
    sheets[n] = s;
  };

  const spreadsheet = {
    getName: () => 'My Assignment Tracker',
    getSheetByName: (n) => sheets[n] || null,
    insertSheet: (n) => {
      sheets[n] = makeSheet(n, [], register);
      return sheets[n];
    },
  };

  const ruleBuilder = () => {
    const state = {};
    const b = {
      whenFormulaSatisfied: (f) => {
        state.formula = f;
        return b;
      },
      setBackground: (v) => {
        state.background = v;
        return b;
      },
      setFontColor: (v) => {
        state.fontColor = v;
        return b;
      },
      setBold: () => b,
      setStrikethrough: (v) => {
        state.strikethrough = v;
        return b;
      },
      setRanges: () => b,
      build: () => state,
    };
    return b;
  };

  const validationBuilder = () => {
    const b = {
      requireValueInList: () => b,
      setAllowInvalid: () => b,
      build: () => ({}),
    };
    return b;
  };

  return {
    sandbox: {
      SpreadsheetApp: {
        getActiveSpreadsheet: () => spreadsheet,
        newConditionalFormatRule: ruleBuilder,
        newDataValidation: validationBuilder,
      },
      ContentService: {
        MimeType: { JSON: 'JSON', TEXT: 'TEXT' },
        createTextOutput: (text) => {
          const out = { _text: text, setMimeType: () => out, getContent: () => out._text };
          return out;
        },
      },
      LockService: { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) },
      console,
    },
    sheets,
    addSheet: (n, rows) => {
      sheets[n] = makeSheet(n, rows, register);
      return sheets[n];
    },
  };
}

/* ------------------------------------------------------------------ running */

// The file keeps a placeholder so the real secret stays out of git;
// copy_receiver.command fills it in on the way to Apps Script.
const source = fs
  .readFileSync(path.join(__dirname, '..', 'sheet_receiver.gs'), 'utf8')
  .replace('PASTE_THE_SECRET_FROM_config.json_HERE', 'test-secret');
const { sandbox, sheets, addSheet } = makeSandbox();
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const SECRET = sandbox.SHARED_SECRET;
const VIEW = 'Assignments';
const DATA = 'Bot Data';
const V = sandbox.V;
const D = sandbox.D;
const FIRST = sandbox.FIRST_DATA_ROW;
const STATUS_LIST = sandbox.STATUS_CHOICES;

let failures = 0;
function check(label, condition, detail) {
  if (condition) console.log(`PASS  ${label}`);
  else {
    failures++;
    console.log(`FAIL  ${label}${detail ? '  -> ' + detail : ''}`);
  }
}

function post(body) {
  return JSON.parse(sandbox.doPost({ postData: { contents: JSON.stringify(body) } }).getContent());
}

function item(key, subject, title, daysLeft, opts) {
  opts = opts || {};
  return {
    key,
    source: 'Canvas',
    course: subject + ' Something Long',
    subject,
    title,
    sort: `2026-${opts.month || '10'}-${opts.day || '18'}T23:59:00-05:00`,
    due: opts.due || 'Sun 18 Oct, 11:59 PM',
    due_on: opts.dueOn || 'Sunday, 10/18',
    days_left: daysLeft,
    status: opts.status || '',
    done: !!opts.done,
    points: '',
    url: 'https://example.test/' + key,
  };
}

function send(items, opts) {
  opts = opts || {};
  return post({
    secret: SECRET,
    action: 'upsert',
    tab: opts.tab || VIEW,
    data_tab: DATA,
    display_within_days: opts.within === undefined ? 7 : opts.within,
    overdue_grace_days: opts.grace === undefined ? 1 : opts.grace,
    done_linger_minutes: opts.linger === undefined ? 30 : opts.linger,
    adopt: !!opts.adopt,
    term: 'Fall 2026',
    items,
  });
}

function viewRows(tab) {
  const sheet = sheets[tab || VIEW];
  if (!sheet) return [];
  return sheet._dump().slice(FIRST - 1).filter((r) => String(r[V.key - 1] || '').trim());
}

/* The hand-made tracker, as it actually looks. */
const HAND_MADE = [
  ['Assignment Tracker', '', '', '', 'Fall 2026'],
  ['', '', '', '', ''],
  ['', 'Days Left key: ...', '', '', ''],
  ['Subject', 'Assignment', 'Status', 'Days Left', 'Due On'],
  ['', '', '', '', ''],
  ['CS 277', 'Quiz 8', 'Not started', 5, 'Sunday, 10/18'],
  ['BIOS 120', 'Evolutionary Medicine Outline', 'Not started', 10, 'Friday, 10/23'],
];

console.log('--- auth ---');
check('wrong secret rejected', post({ secret: 'no', action: 'ping' }).error === 'Wrong shared secret.');
check('ping ok', post({ secret: SECRET, action: 'ping' }).ok === true);

console.log('\n--- without adopt, a tab it does not own is still refused ---');
addSheet(VIEW, HAND_MADE);
const before = JSON.stringify(sheets[VIEW]._dump());
const refused = send([item('x1', 'MATH 220', 'Homework 7', 5)]);
check('refuses', refused.ok === false, JSON.stringify(refused));
check('says how to allow it', /adopt_existing_tab/.test(refused.error || ''), refused.error);
check('tab untouched', JSON.stringify(sheets[VIEW]._dump()) === before);

console.log('\n--- with adopt, it backs the tab up before taking it over ---');
const adopted = send([
  item('near1', 'MATH 220', 'Homework 7', 5),
  item('far1', 'BIOS 120', 'Evolutionary Medicine Paper', 24, { month: '11' }),
], { adopt: true });
check('accepted', adopted.ok === true, JSON.stringify(adopted));
check('reported the backup', /Backed the old tab up/.test((adopted.notes || []).join(' ')), JSON.stringify(adopted.notes));

const backupName = Object.keys(sheets).find((n) => n.indexOf('backup') !== -1);
check('a backup tab exists', !!backupName, Object.keys(sheets).join(' | '));
if (backupName) {
  const backup = sheets[backupName]._dump();
  check('backup holds the original rows',
    backup.some((r) => r[1] === 'Quiz 8') && backup.some((r) => r[1] === 'Evolutionary Medicine Outline'),
    JSON.stringify(backup.map((r) => r[1])));
}

console.log('\n--- the design ---');
const grid = sheets[VIEW]._dump();
check('title in A1', grid[0][V.subject - 1] === 'Assignment Tracker', JSON.stringify(grid[0]));
check('term on the right of the title', grid[0][V.dueOn - 1] === 'Fall 2026');
check('legend on row 3', /Days Left key/.test(String(grid[2][V.assignment - 1])));
check('header row matches the original layout',
  grid[3][V.subject - 1] === 'Subject' && grid[3][V.assignment - 1] === 'Assignment' &&
  grid[3][V.status - 1] === 'Status' && grid[3][V.daysLeft - 1] === 'Days Left' &&
  grid[3][V.dueOn - 1] === 'Due On', JSON.stringify(grid[3]));

console.log('\n--- Days Left stays a formula, Due On stays a date ---');
let rows = viewRows();
check('only the near item is shown', rows.length === 1, JSON.stringify(rows.map((r) => r[V.assignment - 1])));
const formula = String(rows[0][V.daysLeft - 1]);
check('Days Left is the original formula', formula.indexOf('=IF(E5="","",IF(C5="Done","Done"') === 0, formula);
check('formula handles Skipped', /Skipped/.test(formula), formula);
check('formula references its own row only', !/E6|C6/.test(formula), formula);
check('Due On is =DATE(), immune to time zones',
  String(rows[0][V.dueOn - 1]) === '=DATE(2026,10,18)', String(rows[0][V.dueOn - 1]));

console.log('\n--- colours follow the key ---');
const colourRules = sheets[VIEW].getConditionalFormatRules();
check('five rules applied', colourRules.length === 5, 'got ' + colourRules.length);
check('done rows strike through', colourRules[0].strikethrough === true, JSON.stringify(colourRules[0]));
check('overdue and today share the dark red', /Overdue/.test(colourRules[2].formula), colourRules[2].formula);
check('2-7 days red', /<=7/.test(colourRules[3].formula), colourRules[3].formula);
check('8-14 days blue', /<=14/.test(colourRules[4].formula), colourRules[4].formula);

console.log('\n--- the window ---');
check('far work stored but not shown', adopted.rows === 2 && adopted.shown === 1, JSON.stringify(adopted));
let res = send([item('far1', 'BIOS 120', 'Evolutionary Medicine Paper', 6, { month: '11' })], { adopt: true });
check('it arrives once inside the window',
  viewRows().some((r) => r[V.assignment - 1] === 'Evolutionary Medicine Paper'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));

console.log('\n--- your Status edits survive ---');
const at = viewRows().findIndex((r) => r[V.assignment - 1] === 'Homework 7');
sheets[VIEW].getRange(FIRST + at, V.status).setValue('Done');
res = send([item('near1', 'MATH 220', 'Homework 7', 5)], { adopt: true });
const hw7 = viewRows().find((r) => r[V.assignment - 1] === 'Homework 7');
check('your "Done" is kept', hw7[V.status - 1] === 'Done', JSON.stringify(hw7));
check('no second backup on a tab it already owns',
  Object.keys(sheets).filter((n) => n.indexOf('backup') !== -1).length === 1,
  Object.keys(sheets).join(' | '));

console.log('\n--- leaving the window ---');
res = send([item('near1', 'MATH 220', 'Homework 7', 30, { month: '11' })], { adopt: true });
check('gone from the view', !viewRows().some((r) => r[V.assignment - 1] === 'Homework 7'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));
check('still in the store',
  sheets[DATA]._dump().slice(1).some((r) => r[D.assignment - 1] === 'Homework 7'));

console.log('\n--- overdue work drops off after the grace period ---');
const overdue = send([
  item('od0', 'MATH 218', 'Due today', 0, { day: '13' }),
  item('od1', 'MATH 218', 'One day late', -1, { day: '12' }),
  item('od2', 'MATH 218', 'Two days late', -2, { day: '11' }),
  item('od9', 'MATH 218', 'Ancient', -9, { day: '04' }),
], { adopt: true });
let titles = viewRows().map((r) => r[V.assignment - 1]);
check('due today still shown', titles.includes('Due today'), titles.join(' | '));
check('one day late still shown', titles.includes('One day late'), titles.join(' | '));
check('two days late is gone', !titles.includes('Two days late'), titles.join(' | '));
check('long overdue is gone', !titles.includes('Ancient'), titles.join(' | '));
check('they are all still in the store',
  ['Due today', 'One day late', 'Two days late', 'Ancient'].every((t) =>
    sheets[DATA]._dump().slice(1).some((r) => r[D.assignment - 1] === t)),
  'something was dropped from the store');
check('counts reflect the hidden ones', overdue.shown < overdue.rows, JSON.stringify(overdue));

send([], { grace: 14, adopt: true });
titles = viewRows().map((r) => r[V.assignment - 1]);
check('a wider grace brings them back', titles.includes('Ancient'), titles.join(' | '));

send([], { grace: 0, adopt: true });
titles = viewRows().map((r) => r[V.assignment - 1]);
check('grace 0 keeps only today onwards', !titles.includes('One day late') && titles.includes('Due today'), titles.join(' | '));

console.log('\n--- marking something Done makes it go after the linger ---');
send([item('dn1', 'MATH 220', 'Finish me', 3, { day: '16' })], { adopt: true });
let doneAtRow = viewRows().findIndex((r) => r[V.assignment - 1] === 'Finish me');
check('present before being marked', doneAtRow >= 0);

sheets[VIEW].getRange(FIRST + doneAtRow, V.status).setValue('Done');
send([item('dn1', 'MATH 220', 'Finish me', 3, { day: '16' })], { adopt: true });
let row = viewRows().find((r) => r[V.assignment - 1] === 'Finish me');
check('still shown immediately after marking Done', !!row, 'vanished too early');
check('and it kept your Done', row && row[V.status - 1] === 'Done', JSON.stringify(row));

const doneRow = sheets[DATA]._dump().slice(1).find((r) => r[D.assignment - 1] === 'Finish me');
check('a Done-at time was recorded',
  Object.prototype.toString.call(doneRow[D.doneAt - 1]) === '[object Date]',
  String(doneRow[D.doneAt - 1]));

// Wind the clock back so the linger has elapsed. _dump() hands back copies,
// so this has to be written through the sheet to count.
const doneLine = sheets[DATA]._dump().findIndex((r) => r[D.assignment - 1] === 'Finish me') + 1;
sheets[DATA].getRange(doneLine, D.doneAt).setValue(new Date(Date.now() - 31 * 60 * 1000));
send([item('dn1', 'MATH 220', 'Finish me', 3, { day: '16' })], { adopt: true });
check('gone once 30 minutes have passed',
  !viewRows().some((r) => r[V.assignment - 1] === 'Finish me'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));
check('but still kept in the store',
  sheets[DATA]._dump().slice(1).some((r) => r[D.assignment - 1] === 'Finish me'));

console.log('\n--- un-marking Done brings it back and restarts the clock ---');
send([item('dn2', 'CS 277', 'Changed my mind', 4, { day: '17' })], { adopt: true });
doneAtRow = viewRows().findIndex((r) => r[V.assignment - 1] === 'Changed my mind');
sheets[VIEW].getRange(FIRST + doneAtRow, V.status).setValue('Done');
send([item('dn2', 'CS 277', 'Changed my mind', 4, { day: '17' })], { adopt: true });

doneAtRow = viewRows().findIndex((r) => r[V.assignment - 1] === 'Changed my mind');
sheets[VIEW].getRange(FIRST + doneAtRow, V.status).setValue('In progress');
send([item('dn2', 'CS 277', 'Changed my mind', 4, { day: '17' })], { adopt: true });

const back = sheets[DATA]._dump().slice(1).find((r) => r[D.assignment - 1] === 'Changed my mind');
check('the Done-at time was cleared', !(back[D.doneAt - 1] instanceof Date), String(back[D.doneAt - 1]));
check('it stays on the tracker',
  viewRows().some((r) => r[V.assignment - 1] === 'Changed my mind'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));

console.log('\n--- a finished row must not come back (regression) ---');
// The bug: your "Done" lived only in the view. Once the finished row was
// removed, the next run saw a source reporting no status at all, decided the
// work was unfinished, and put it back as "Not started".
send([item('rb1', 'CS 277', 'W8 Quiz', 3, { day: '18' })], { adopt: true });
let rbRow = viewRows().findIndex((r) => r[V.assignment - 1] === 'W8 Quiz');
check('shown before you touch it', rbRow >= 0);

sheets[VIEW].getRange(FIRST + rbRow, V.status).setValue('Done');
send([item('rb1', 'CS 277', 'W8 Quiz', 3, { day: '18' })], { adopt: true });
check('your Done is stored, not just displayed',
  sheets[DATA]._dump().slice(1).some(
    (r) => r[D.assignment - 1] === 'W8 Quiz' && String(r[D.yourStatus - 1]) === 'Done'),
  'Your status column was not written');

const rbLine = sheets[DATA]._dump().findIndex((r) => r[D.assignment - 1] === 'W8 Quiz') + 1;
sheets[DATA].getRange(rbLine, D.doneAt).setValue(new Date(Date.now() - 31 * 60 * 1000));
send([item('rb1', 'CS 277', 'W8 Quiz', 3, { day: '18' })], { adopt: true });
check('gone after the linger',
  !viewRows().some((r) => r[V.assignment - 1] === 'W8 Quiz'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));

// The run that used to resurrect it.
send([item('rb1', 'CS 277', 'W8 Quiz', 3, { day: '18' })], { adopt: true });
check('STAYS gone on the next run',
  !viewRows().some((r) => r[V.assignment - 1] === 'W8 Quiz'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));
send([item('rb1', 'CS 277', 'W8 Quiz', 3, { day: '18' })], { adopt: true });
check('and the one after that',
  !viewRows().some((r) => r[V.assignment - 1] === 'W8 Quiz'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));

console.log('\n--- clearing Your status in the store brings a row back ---');
const rbLine2 = sheets[DATA]._dump().findIndex((r) => r[D.assignment - 1] === 'W8 Quiz') + 1;
sheets[DATA].getRange(rbLine2, D.yourStatus).setValue('');
sheets[DATA].getRange(rbLine2, D.doneAt).setValue('');
send([item('rb1', 'CS 277', 'W8 Quiz', 3, { day: '18' })], { adopt: true });
check('back on the tracker',
  viewRows().some((r) => r[V.assignment - 1] === 'W8 Quiz'),
  viewRows().map((r) => r[V.assignment - 1]).join(' | '));

console.log('\n--- a moved column rebuilds the store instead of misreading it ---');
// Inserting 'Your status' mid-schema once shifted every stored row by one, and
// Points surfaced as a Status on the tracker.
const DH = sandbox.DATA_HEADERS;
const shifted = DH.slice(0, 9).concat(['Points'], DH.slice(9, DH.length - 1));
addSheet(DATA, [shifted, ['k1', '2026-10-18T23:59:00-05:00', 'c', 'MATH 220', 'Old row']]);
const rebuilt = send([item('sc1', 'MATH 220', 'After rebuild', 3, { day: '18' })], { adopt: true });
check('accepted', rebuilt.ok === true, JSON.stringify(rebuilt));
check('said it rebuilt the store',
  /Rebuilt/.test((rebuilt.notes || []).join(' ')), JSON.stringify(rebuilt.notes));
check('the misaligned row is gone',
  !sheets[DATA]._dump().slice(1).some((r) => r[D.assignment - 1] === 'Old row'),
  'a row with the old column layout survived');
check('header is the current schema',
  sheets[DATA]._dump()[0].join('|') === DH.join('|'), sheets[DATA]._dump()[0].join('|'));

console.log('\n--- appending a column keeps the rows ---');
const narrower = DH.slice(0, DH.length - 1);
addSheet(DATA, [narrower, ['k9', '2026-10-18T23:59:00-05:00', 'c', 'CS 277', 'Keep me']]);
const appended = send([item('sc2', 'CS 277', 'Another', 3, { day: '18' })], { adopt: true });
check('no rebuild announced', !/Rebuilt/.test((appended.notes || []).join(' ')),
  JSON.stringify(appended.notes));
check('the existing row survived',
  sheets[DATA]._dump().slice(1).some((r) => r[D.assignment - 1] === 'Keep me'),
  'an append wrongly wiped the store');

console.log('\n--- junk in the Status cell is not treated as your choice ---');
send([item('jk1', 'MATH 218', 'Homework 4', 5, { day: '20' })], { adopt: true });
const jkRow = viewRows().findIndex((r) => r[V.assignment - 1] === 'Homework 4');
sheets[VIEW].getRange(FIRST + jkRow, V.status).setValue('20');   // a points value
send([item('jk1', 'MATH 218', 'Homework 4', 5, { day: '20' })], { adopt: true });

const jk = viewRows().find((r) => r[V.assignment - 1] === 'Homework 4');
check('the junk did not stick', jk[V.status - 1] !== '20', JSON.stringify(jk));
check('it fell back to a real status',
  STATUS_LIST.indexOf(String(jk[V.status - 1])) !== -1, String(jk[V.status - 1]));
check('and it was not saved to the store',
  !sheets[DATA]._dump().slice(1).some(
    (r) => r[D.assignment - 1] === 'Homework 4' && String(r[D.yourStatus - 1]) === '20'),
  'junk reached Your status');

console.log('\n--- a real status still counts ---');
const okRow = viewRows().findIndex((r) => r[V.assignment - 1] === 'Homework 4');
sheets[VIEW].getRange(FIRST + okRow, V.status).setValue('In progress');
send([item('jk1', 'MATH 218', 'Homework 4', 5, { day: '20' })], { adopt: true });
check('your real edit is kept',
  viewRows().find((r) => r[V.assignment - 1] === 'Homework 4')[V.status - 1] === 'In progress',
  JSON.stringify(viewRows().find((r) => r[V.assignment - 1] === 'Homework 4')));

console.log('\n--- malformed input ---');
check('empty items ok', send([], { adopt: true }).ok === true);
check('item without a key skipped', send([{ title: 'x' }], { adopt: true }).added === 0);
const noPost = JSON.parse(sandbox.doPost({}).getContent());
check('missing postData handled', noPost.ok === false && /Empty request/.test(noPost.error));
check('unknown action rejected', /Unknown action/.test(post({ secret: SECRET, action: 'wat' }).error));

console.log('\n' + (failures ? `${failures} FAILURE(S)` : 'All receiver checks passed.'));
process.exit(failures ? 1 : 0);
