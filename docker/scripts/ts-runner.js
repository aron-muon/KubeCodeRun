#!/usr/bin/env node
/**
 * TypeScript runner for shell-less execution.
 * Compiles TypeScript with tsc and runs the output with Node.js.
 *
 * Usage: node /opt/scripts/ts-runner.js <file.ts>
 */
const { execFileSync } = require('child_process');
const path = require('path');

const file = process.argv[2];
if (!file) {
  console.error('Usage: ts-runner.js <file.ts>');
  process.exit(1);
}

const outDir = '/tmp';
const baseName = path.basename(file, '.ts');
const outFile = path.join(outDir, baseName + '.js');

// Compile TypeScript
execFileSync('tsc', [file, '--outDir', outDir, '--module', 'commonjs', '--target', 'ES2019'], {
  stdio: 'inherit'
});

// Run the compiled JavaScript
require(outFile);
