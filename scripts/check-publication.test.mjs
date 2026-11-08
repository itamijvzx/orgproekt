import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const scratch = join(project, '.local')
mkdirSync(scratch, { recursive: true })

function fixture(t) {
  const root = mkdtempSync(join(scratch, 'publication-test-'))
  t.after(() => {
    assert.equal(dirname(root), scratch)
    rmSync(root, { recursive: true, force: true })
  })
  mkdirSync(join(root, 'scripts'))
  copyFileSync(join(project, 'scripts/check-publication.mjs'), join(root, 'scripts/check-publication.mjs'))
  assert.equal(spawnSync('git', ['init', '--quiet'], { cwd: root }).status, 0)
  writeFileSync(join(root, '.gitignore'), '.local/\n.env\n*.pdf\n')
  writeFileSync(join(root, 'README.md'), 'Synthetic source\n')
  return root
}

function run(root, args = []) {
  return spawnSync(process.execPath, ['scripts/check-publication.mjs', ...args], { cwd: root, encoding: 'utf8' })
}

test('exports only Git candidates and preserves ignored files', t => {
  const root = fixture(t)
  const secret = 'synthetic-secret-must-stay-local'
  writeFileSync(join(root, '.env'), secret)
  writeFileSync(join(root, 'personal.pdf'), 'Synthetic private document')
  const checked = run(root, ['--export'])
  assert.equal(checked.status, 0, checked.stderr)
  const { source, files } = JSON.parse(checked.stdout)
  assert.equal(files, 3)
  assert.equal(readFileSync(join(source, 'README.md'), 'utf8'), 'Synthetic source\n')
  assert.equal(existsSync(join(source, '.env')), false)
  assert.equal(existsSync(join(source, 'personal.pdf')), false)
  assert.equal(existsSync(join(source, '.git')), false)
  assert.equal(readFileSync(join(root, '.env'), 'utf8'), secret)
  assert.equal(checked.stdout.includes(secret), false)
})

for (const name of ['.env', 'personal.pdf', 'account.key', 'screenshots/login.png']) {
  test('rejects a forcibly tracked sensitive file: ' + name, t => {
    const root = fixture(t)
    const secret = 'synthetic-secret-must-not-be-printed'
    mkdirSync(dirname(join(root, name)), { recursive: true })
    writeFileSync(join(root, name), secret)
    assert.equal(spawnSync('git', ['add', '--force', '--', name], { cwd: root }).status, 0)
    const checked = run(root, ['--export'])
    assert.equal(checked.status, 1)
    assert.ok(checked.stderr.includes(name))
    assert.equal((checked.stdout + checked.stderr).includes(secret), false)
  })
}
