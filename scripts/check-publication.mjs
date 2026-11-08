/** Review Git's actual file selection, including forcibly tracked ignored files.
 * Optional --export makes a new source-only copy in .local for clean-start checks.
 * Content scanning with Gitleaks and review of allowed documents are separate checks.
 */
import { execFileSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const documents = new Set([
  'frontend/e2e/fixtures/synthetic-regulation.docx',
  'frontend/e2e/fixtures/synthetic-regulation-after.docx',
  'frontend/e2e/fixtures/synthetic-regulation.pdf',
  'frontend/e2e/fixtures/synthetic-scan.pdf',
])
const screenshots = new Set([
  'docs/screenshots/17-auditor-overview-1440.png',
  'docs/screenshots/14-hybrid-search-1440.png',
  'docs/screenshots/42-report-released-1440.png',
])
const privateDirectory = /(^|\/)(\.git|\.local|\.aws|\.ssh|\.codex|\.agents|\.venv|node_modules|uploads|backups|test-results|playwright-report|dist)(\/|$)/i
const privateName = /(^|\/)(\.env($|\.)|demo-accounts\.json$|workflow-accounts\.json$|test-factors\.json$|password-reset\.txt$|audit-seal\.json$)/i
const unsafeExtension = /\.(key|pem|p12|pfx|agekey|age|dump|sqlite\d*|db|zip|tar|gz|7z|log|docx?|pdf|xlsx?|pptx?|od[ts]|rtf)$/i
const imageExtension = /\.(png|jpe?g|gif|webp)$/i

function gitFiles(args) {
  return execFileSync('git', ['ls-files', '-z', ...args], { cwd: root, encoding: 'utf8' })
    .split('\0').filter(Boolean)
}

function check() {
  if (process.argv.length > 3 || (process.argv[2] && process.argv[2] !== '--export')) {
    throw new Error('Usage: node scripts/check-publication.mjs [--export]')
  }
  const files = [...new Set([...gitFiles(['--cached']), ...gitFiles(['--others', '--exclude-standard'])])].sort()
  const rejected = []
  for (const file of files) {
    const target = resolve(root, file)
    const special = documents.has(file) || screenshots.has(file)
    const unsafe = privateDirectory.test(file) ||
      (file !== '.env.example' && privateName.test(file)) ||
      (!special && (unsafeExtension.test(file) || imageExtension.test(file)))
    if (unsafe || relative(root, target).startsWith('..') || !existsSync(target) ||
      !lstatSync(target).isFile() || lstatSync(target).isSymbolicLink() ||
      realpathSync(target) !== target) rejected.push(file)
  }
  if (rejected.length) {
    throw new Error('Files require removal from the Git selection or explicit review:\n' + rejected.join('\n'))
  }
  if (!files.length) throw new Error('The Git file selection is empty')
  const result = { result: 'passed', files: files.length, public_documents: files.filter(f => documents.has(f)).length }
  if (process.argv[2] === '--export') {
    const privateRoot = join(root, '.local')
    mkdirSync(privateRoot, { recursive: true })
    if (realpathSync(privateRoot) !== privateRoot) throw new Error('Unsafe private export directory')
    const directory = join(privateRoot, 'publication-' + randomUUID())
    const source = join(directory, 'source')
    mkdirSync(source, { recursive: true })
    const manifest = []
    for (const file of files) {
      const destination = join(source, file)
      mkdirSync(dirname(destination), { recursive: true })
      copyFileSync(join(root, file), destination)
      manifest.push({ file, sha256: createHash('sha256').update(readFileSync(destination)).digest('hex') })
    }
    writeFileSync(join(directory, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
    result.source = source
  }
  process.stdout.write(JSON.stringify(result) + '\n')
}

try { check() } catch (error) {
  console.error(error.message)
  process.exitCode = 1
}
