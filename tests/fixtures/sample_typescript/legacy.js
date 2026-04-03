/**
 * CommonJS legacy module.
 * Tests: CommonJS require() — both external (fs) and relative (./utils).
 */
const fs = require('fs');
const path = require('path');
const utils = require('./utils');

function readConfig(filePath) {
  const content = fs.readFileSync(path.resolve(filePath), 'utf-8');
  return JSON.parse(content);
}

function processData(data) {
  return utils.helper(String(data));
}

module.exports = { readConfig, processData };
