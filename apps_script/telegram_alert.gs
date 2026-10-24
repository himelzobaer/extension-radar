/**
 * Extension Opportunity Radar: Telegram alert.
 *
 * Reads unsent rows from the Alerts tab, sends a summary to Telegram,
 * then marks them sent so the same item never comes twice.
 *
 * Script Properties (Project Settings > Script Properties):
 *   TELEGRAM_BOT_TOKEN  bot token from @BotFather
 *   TELEGRAM_CHAT_ID    your chat id
 *   SHEET_ID            (optional) only needed if this script is NOT bound to the sheet
 *
 * Setup: run installTrigger() once. It checks for new alerts every hour.
 */

var ALERTS_TAB = 'Alerts';
var TOP_N = 5;
var MAX_MESSAGE_CHARS = 3900; // Telegram limit is 4096

function sendNewAlerts() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('TELEGRAM_BOT_TOKEN');
  var chatId = props.getProperty('TELEGRAM_CHAT_ID');
  if (!token || !chatId) {
    throw new Error('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Script Properties');
  }

  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  try {
    var sheet = openSheet_(props).getSheetByName(ALERTS_TAB);
    if (!sheet) throw new Error('Alerts tab not found');
    var values = sheet.getDataRange().getValues();
    if (values.length < 2) return;

    var header = values[0];
    var col = {};
    header.forEach(function (h, i) { col[h] = i; });
    ['flag_type', 'name', 'users', 'link', 'sent', 'sent_at'].forEach(function (c) {
      if (col[c] === undefined) throw new Error('Alerts tab missing column ' + c);
    });

    var pending = [];
    for (var r = 1; r < values.length; r++) {
      var sent = String(values[r][col.sent]).toUpperCase();
      if (sent !== 'TRUE' && values[r][col.flag_type]) {
        pending.push({ row: r + 1, flag: String(values[r][col.flag_type]), name: String(values[r][col.name]),
                       users: Number(values[r][col.users]) || 0, link: String(values[r][col.link]) });
      }
    }
    if (pending.length === 0) return;

    sendTelegram_(token, chatId, buildMessage_(pending));

    var now = new Date().toISOString();
    pending.forEach(function (p) {
      sheet.getRange(p.row, col.sent + 1).setValue('TRUE');
      sheet.getRange(p.row, col.sent_at + 1).setValue(now);
    });
  } finally {
    lock.releaseLock();
  }
}

function buildMessage_(pending) {
  var byFlag = {};
  pending.forEach(function (p) { (byFlag[p.flag] = byFlag[p.flag] || []).push(p); });

  var lines = ['<b>Extension Radar: ' + pending.length + ' new item</b>', ''];
  Object.keys(byFlag).sort().forEach(function (flag) {
    lines.push('<b>' + esc_(flag) + '</b>: ' + byFlag[flag].length);
  });
  Object.keys(byFlag).sort().forEach(function (flag) {
    var top = byFlag[flag].sort(function (a, b) { return b.users - a.users; }).slice(0, TOP_N);
    lines.push('', '<b>Top ' + top.length + ' ' + esc_(flag) + '</b>');
    top.forEach(function (p, i) {
      lines.push((i + 1) + '. <a href="' + esc_(p.link) + '">' + esc_(p.name) + '</a> (' +
                 p.users.toLocaleString('en-US') + ')');
    });
  });
  var text = lines.join('\n');
  return text.length > MAX_MESSAGE_CHARS ? text.slice(0, MAX_MESSAGE_CHARS) + '\n...' : text;
}

function sendTelegram_(token, chatId, text) {
  var resp = UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ chat_id: chatId, text: text, parse_mode: 'HTML',
                              disable_web_page_preview: true }),
    muteHttpExceptions: true
  });
  if (resp.getResponseCode() !== 200) {
    // Do not include the token in the error.
    throw new Error('Telegram send failed: HTTP ' + resp.getResponseCode() + ' ' + resp.getContentText().slice(0, 200));
  }
}

function openSheet_(props) {
  var id = props.getProperty('SHEET_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
}

function esc_(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Run once: hourly trigger for sendNewAlerts (removes old copies first). */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sendNewAlerts') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sendNewAlerts').timeBased().everyHours(1).create();
}

/** Manual test: sends one test message without touching the sheet. */
function sendTestMessage() {
  var props = PropertiesService.getScriptProperties();
  sendTelegram_(props.getProperty('TELEGRAM_BOT_TOKEN'), props.getProperty('TELEGRAM_CHAT_ID'),
                'Extension Radar: test message OK');
}
