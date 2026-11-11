import assert from 'node:assert/strict'
import test from 'node:test'
import { evaluate } from './check-image-scan.mjs'

function fixture() {
  const report = {
    SchemaVersion: 2, Metadata: { ImageID: 'sha256:fixture' }, Results: [{ Target: 'Python',
      Packages: [{ Name: 'example', Version: '1.0', AnalyzedBy: 'sbom' }],
      Vulnerabilities: [{ VulnerabilityID: 'CVE-TEST', PkgName: 'example', InstalledVersion: '1.0',
        FixedVersion: '2.0', Severity: 'HIGH' }],
    }],
  }
  const policy = { version: 1, exceptions: [{ images: ['backend'], ids: ['CVE-TEST'], target: 'Python',
    package: 'example', installed: '1.0', sbom_only: true, expires: '2026-12-11',
    reason: 'Verified build-only fixture', source: 'https://example.invalid/advisory' }] }
  return { report, policy }
}

test('only a fully matched build-only exception passes', () => {
  const { report, policy } = fixture()
  assert.equal(evaluate(report, policy, 'backend', '2026-11-11').excepted.length, 1)
  for (const change of [
    rule => { rule.images = ['caddy'] }, rule => { rule.target = 'elsewhere' },
    rule => { rule.package = 'another' }, rule => { rule.installed = '0.9' },
    rule => { rule.ids = ['CVE-OTHER'] }, rule => { rule.expires = '2026-11-10' },
  ]) {
    const altered = structuredClone(policy)
    change(altered.exceptions[0])
    assert.equal(evaluate(report, altered, 'backend', '2026-11-11').blocked.length, 1)
  }
})

test('real installed packages and missing evidence cannot use a SBOM exception', () => {
  const { report, policy } = fixture()
  report.Results[0].Packages.push({ Name: 'example', Version: '1.0', AnalyzedBy: 'python-pkg', FilePath: 'METADATA' })
  assert.equal(evaluate(report, policy, 'backend', '2026-11-11').blocked.length, 1)
  report.Results[0].Packages = []
  assert.equal(evaluate(report, policy, 'backend', '2026-11-11').blocked.length, 1)
})

test('unknown patched critical issues block; no-fix issues stay visible', () => {
  const { report, policy } = fixture()
  const finding = report.Results[0].Vulnerabilities[0]
  finding.VulnerabilityID = 'CVE-NEW'
  finding.Severity = 'CRITICAL'
  assert.equal(evaluate(report, policy, 'backend', '2026-11-11').blocked.length, 1)
  finding.FixedVersion = ''
  assert.equal(evaluate(report, policy, 'backend', '2026-11-11').withoutFix.length, 1)
})

test('missing scanner output or malformed policy fails closed', () => {
  const { report, policy } = fixture()
  for (const malformed of [{}, { ...report, Results: [] }, { ...report, Metadata: {} }]) {
    assert.throws(() => evaluate(malformed, policy, 'backend'))
  }
  delete policy.exceptions[0].reason
  assert.throws(() => evaluate(report, policy, 'backend'))
})
