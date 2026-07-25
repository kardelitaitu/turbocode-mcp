const assert = require('node:assert');
const { describe, it } = require('node:test');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const SETUP_SCRIPT = path.join(ROOT, 'scripts', 'setup.js');

describe('Setup Script', () => {
  it('should exist', () => {
    assert.ok(fs.existsSync(SETUP_SCRIPT));
  });

  it('should resolve paths relative to __dirname', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes("path.join(__dirname, '..')"));
  });

  it('should detect platform for venv paths', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes('IS_WIN'));
    assert.ok(content.includes('VENV_DIR'));
    assert.ok(content.includes('REQUIREMENTS'));
  });

  it('should handle both python and python3', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes("'python'"));
    assert.ok(content.includes("'python3'"));
  });

  it('.venv should contain Python after postinstall', () => {
    const pythonBin = process.platform === 'win32'
      ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
      : path.join(ROOT, '.venv', 'bin', 'python');
    assert.ok(fs.existsSync(pythonBin), '.venv Python not found');
  });

  it('.venv should contain pip after postinstall', () => {
    const pipBin = process.platform === 'win32'
      ? path.join(ROOT, '.venv', 'Scripts', 'pip.exe')
      : path.join(ROOT, '.venv', 'bin', 'pip');
    assert.ok(fs.existsSync(pipBin), '.venv pip not found');
  });

  it('requirements.txt should list all 4 dependencies', () => {
    const reqs = fs.readFileSync(path.join(ROOT, 'requirements.txt'), 'utf-8');
    assert.ok(reqs.includes('fastmcp'));
    assert.ok(reqs.includes('turbovec'));
    assert.ok(reqs.includes('sentence-transformers'));
    assert.ok(reqs.includes('numpy'));
  });

  it('should install required packages in .venv', () => {
    const result = require('child_process').execSync(
      `"${path.join(ROOT, '.venv', 'Scripts', 'pip.exe')}" list --format=columns`,
      { encoding: 'utf-8' }
    );
    assert.ok(result.includes('fastmcp'));
    assert.ok(result.includes('turbovec'));
    assert.ok(result.includes('sentence'));
    assert.ok(result.includes('numpy'));
  });

  it('should exit on Python not found', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes("Python not found"));
    assert.ok(content.includes('process.exit(1)'));
  });

  it('should exit on Python version too old', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes('major > 3 || (major === 3 && minor >= 9)'));
    assert.ok(content.includes('process.exit(1)'));
  });

  it('should exit on missing pip', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes('pip not found'));
    assert.ok(content.includes('process.exit(1)'));
  });

  it('should handle missing requirements.txt gracefully', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes("requirements.txt not found"));
  });

  it('should verify setup after completion', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes('pythonBin'));
    assert.ok(content.includes('fs.existsSync(pythonBin)'));
    assert.ok(content.includes('process.exit(1)'));
  });

  it('findPython rejects version too old (test regex)', () => {
    // Test the regex pattern used in findPython
    const pattern = /Python (\d+)\.(\d+)/;
    const match3_8 = 'Python 3.8.10'.match(pattern);
    assert.ok(match3_8);
    assert.strictEqual(parseInt(match3_8[1], 10), 3);
    assert.strictEqual(parseInt(match3_8[2], 10), 8);
    // 3.8 should fail the check
    assert.ok(!(parseInt(match3_8[1], 10) > 3 || (parseInt(match3_8[1], 10) === 3 && parseInt(match3_8[2], 10) >= 9)));

    const match3_12 = 'Python 3.12.0'.match(pattern);
    assert.ok(match3_12);
    assert.strictEqual(parseInt(match3_12[1], 10), 3);
    assert.strictEqual(parseInt(match3_12[2], 10), 12);
    // 3.12 should pass
    assert.ok(parseInt(match3_12[1], 10) > 3 || (parseInt(match3_12[1], 10) === 3 && parseInt(match3_12[2], 10) >= 9));
  });

  it('findPython edge cases (no match, empty output, wrong format)', () => {
    const pattern = /Python (\d+)\.(\d+)/;
    // Non-Python output
    assert.ok(!'node v20.0.0'.match(pattern));
    // Missing minor version
    assert.ok(!'Python 3'.match(pattern));
    // Weird spacing
    assert.ok(!'Python3.10'.match(pattern));
    // Empty string
    assert.ok(!''.match(pattern));
  });

  it('should handle venv already exists case', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes('fs.existsSync(VENV_DIR)'));
    assert.ok(content.includes('Virtual environment already exists.'));
    assert.ok(content.includes('Virtual environment created.'));
  });

  it('should verify requirements.txt fallback path', () => {
    const content = fs.readFileSync(SETUP_SCRIPT, 'utf-8');
    assert.ok(content.includes('REQUIREMENTS'));
    assert.ok(content.includes("requirements.txt not found, installing default packages"));
  });
});
