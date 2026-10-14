/**
 * Verifies cleanup_old_rows.gs against a replica of a damaged tab:
 * hand-made rows interleaved with bot rows, exactly as the early receiver
 * left them. It must remove every bot row and not one of the user's.
 *
 *   node tests/cleanup_harness.js
 */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

function makeSheet(name, rows) {
  const cells = rows.map((r) => r.slice());

  const get = (r, c) =>
    cells[r - 1] && cells[r - 1][c - 1] !== undefined ? cells[r - 1][c - 1] : '';

  function makeRange(r, c, nr, nc) {
    return {
      getValue: () => get(r, c),
      getValues: () => {
        const out = [];
        for (let i = 0; i < nr; i++) {
          const row = [];
          for (let j = 0; j < nc; j++) row.push(get(r + i, c + j));
          out.push(row);
        }
        return out;
      },
    };
  }

  return {
    getName: () => name,
    getLastRow: () => cells.length,
    getRange: (r, c, nr, nc) =>
      makeRange(r, c, nr === undefined ? 1 : nr, nc === undefined ? 1 : nc),
    deleteRow: (r) => {
      cells.splice(r - 1, 1);
    },
    _dump: () => cells.map((r) => r.slice()),
  };
}

// A bot row carries a 16-hex key in column A; a hand-made row does not.
const DAMAGED = [
  ['Key', 'Source', 'Course', 'Assignment', 'Due', 'Status'],
  ['5d898991aa5fc55d', 'Gradescope', 'Math 220', 'Homework 6 (due 10/11)', 'Mon 12 Oct', 'Submitted'],
  ['', 'Written Homework #3', 'Done', 'Done', 'Tue 13 Oct', ''],
  ['a1b2c3d4e5f60718', 'Gradescope', 'Math 218', 'Written Homework #3', 'Fri 16 Oct', 'Submitted'],
  ['', 'Quiz 8', 'Not started', '5', 'Sun 18 Oct', ''],
  ['', 'Sec 4.7', 'Not started', '5', 'Sun 18 Oct', ''],
  ['', 'Homework 7', 'Not started', '5', 'Sun 18 Oct', ''],
  ['0f1e2d3c4b5a6978', 'Gradescope', 'Math 220', 'Homework 7 (due 10/18)', 'Mon 19 Oct', 'No Submission'],
  ['', 'MOM Homework 4', 'Not started', '7', 'Tue 20 Oct', ''],
  ['', 'Exam 2', 'Exam/Quiz', '15', 'Wed 28 Oct', ''],
  ['', 'Days Left key: Dark red = Overdue', '', '', '', ''],
  ['', 'Assignment Tracker', '', '', 'Fall 2026', ''],
];

const sheet = makeSheet('Assignments', DAMAGED);
const alerts = [];

const sandbox = {
  SpreadsheetApp: {
    getActiveSpreadsheet: () => ({ getSheetByName: (n) => (n === 'Assignments' ? sheet : null) }),
    getUi: () => ({ alert: (m) => alerts.push(m) }),
  },
  Logger: { log: () => {} },
};

vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(path.join(__dirname, '..', 'cleanup_old_rows.gs'), 'utf8'),
  sandbox
);

let failures = 0;
function check(label, condition, detail) {
  if (condition) console.log(`PASS  ${label}`);
  else {
    failures++;
    console.log(`FAIL  ${label}${detail ? '  -> ' + detail : ''}`);
  }
}

const before = sheet._dump().length;

sandbox.previewCleanup();
check('preview reports the right count', /3 bot row\(s\)/.test(alerts[0]), alerts[0]);
check('preview changes nothing', sheet._dump().length === before);

sandbox.removeBotRows();
const after = sheet._dump();

check('bot rows gone', !after.some((r) => /^[0-9a-f]{16}$/.test(String(r[0]))), JSON.stringify(after.map((r) => r[0])));
check('inserted header gone', after[0][0] !== 'Key', JSON.stringify(after[0]));

const kept = after.map((r) => r[1]);
for (const name of [
  'Written Homework #3',
  'Quiz 8',
  'Sec 4.7',
  'Homework 7',
  'MOM Homework 4',
  'Exam 2',
  'Assignment Tracker',
]) {
  check(`kept "${name}"`, kept.includes(name), JSON.stringify(kept));
}
check('kept the legend row', kept.some((v) => String(v).startsWith('Days Left key')));
check('row count is right', after.length === 8, `got ${after.length}`);

console.log('\n' + (failures ? `${failures} FAILURE(S)` : 'All cleanup checks passed.'));
process.exit(failures ? 1 : 0);
