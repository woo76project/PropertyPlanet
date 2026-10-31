import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("proxy accepts a runtime secret supplied after the proxy module loads", () => {
  // A fresh process keeps Clerk's cached defaults isolated from other tests.
  // Dummy credentials and a signed-out request require no Clerk API calls.
  const result = spawnSync(process.execPath, ["-e", `
    const assert = require('node:assert/strict');
    const fs = require('node:fs');
    const path = require('node:path');
    const Module = require('node:module');
    const ts = require('typescript');
    delete process.env.CLERK_SECRET_KEY;
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY =
      'pk_test_' + Buffer.from('example.clerk.accounts.dev$').toString('base64');
    process.env.CLERK_TELEMETRY_DISABLED = '1';
    const sourcePath = path.resolve('src/proxy.ts');
    const code = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
    }).outputText;
    const compiled = new Module(sourcePath, module);
    compiled.filename = sourcePath;
    compiled.paths = Module._nodeModulePaths(path.dirname(sourcePath));
    compiled._compile(code, sourcePath);
    process.env.CLERK_SECRET_KEY = 'sk_test_mock_runtime_secret';
    const { NextRequest } = require('next/server');
    (async () => {
      const response = await compiled.exports.default(
        new NextRequest('http://localhost:3000/'), { waitUntil() {} }
      );
      assert.equal(response.status, 200);
    })().catch(() => { console.error('Proxy rejected the late runtime secret'); process.exitCode = 1; });
  `], {
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    encoding: "utf8",
    timeout: 15000,
  });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
});
