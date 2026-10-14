/**
 * ONE-OFF CLEANUP - only needed if an early version of the bot wrote into a
 * tab of your own.
 *
 * Use Google's version history first if you can: File -> Version history ->
 * See version history -> restore a version from before the bot ran. That is
 * the only thing that puts your original ROW ORDER back, because the bot
 * sorted the tab and that ordering is not recoverable from the sheet itself.
 *
 * This script is the fallback. It removes the rows the bot added and the
 * header row it inserted, leaving your own rows - but in whatever order the
 * sort left them.
 *
 * HOW TO USE
 *   1. In Apps Script, click + next to Files, choose Script, and paste this in.
 *      (Keep it separate from sheet_receiver.gs so your deployment is unchanged.)
 *   2. Set TAB_TO_CLEAN below to the tab that was damaged.
 *   3. Run previewCleanup() first - it only reports, it changes nothing.
 *   4. If the report looks right, run removeBotRows().
 */

var TAB_TO_CLEAN = 'Assignments';

/** The bot writes a 16-character hex id into column A of every row it owns. */
var BOT_KEY = /^[0-9a-f]{16}$/;

/** Reports what would be removed. Changes nothing. */
function previewCleanup() {
  var found = findBotRows();
  if (!found) {
    return;
  }
  var message =
    'Tab "' + TAB_TO_CLEAN + '"\n' +
    found.rows.length + ' bot row(s) would be removed.\n' +
    (found.header ? 'The inserted header row would also be removed.\n' : '') +
    'Rows kept: ' + found.kept + '\n\n' +
    'First few that would go:\n' + found.sample.join('\n');
  Logger.log(message);
  SpreadsheetApp.getUi().alert(message);
}

/** Actually removes them. */
function removeBotRows() {
  var found = findBotRows();
  if (!found) {
    return;
  }

  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(TAB_TO_CLEAN);

  // Delete from the bottom up, so earlier row numbers stay valid.
  var numbers = found.rows.slice().sort(function (a, b) { return b - a; });
  for (var i = 0; i < numbers.length; i++) {
    sheet.deleteRow(numbers[i]);
  }
  if (found.header) {
    sheet.deleteRow(1);
  }

  var message = 'Removed ' + numbers.length + ' bot row(s)' +
    (found.header ? ' and the inserted header row' : '') + '.';
  Logger.log(message);
  SpreadsheetApp.getUi().alert(message);
}

function findBotRows() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(TAB_TO_CLEAN);
  if (!sheet) {
    SpreadsheetApp.getUi().alert('No tab named "' + TAB_TO_CLEAN + '".');
    return null;
  }

  var lastRow = sheet.getLastRow();
  if (lastRow < 1) {
    SpreadsheetApp.getUi().alert('That tab is empty.');
    return null;
  }

  var columnA = sheet.getRange(1, 1, lastRow, 1).getValues();
  var rows = [];
  var sample = [];
  var kept = 0;

  for (var i = 0; i < columnA.length; i++) {
    var value = String(columnA[i][0] || '').trim();
    if (BOT_KEY.test(value)) {
      rows.push(i + 1);
      if (sample.length < 5) {
        // Column D held the assignment name in the old layout.
        sample.push('  row ' + (i + 1) + ': ' + sheet.getRange(i + 1, 4).getValue());
      }
    } else {
      kept++;
    }
  }

  var header = String(sheet.getRange(1, 1).getValue()).trim() === 'Key';
  if (header) {
    kept--;
  }

  return { rows: rows, header: header, kept: kept, sample: sample };
}
