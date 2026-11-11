/** Fail on actionable HIGH/CRITICAL findings; retain every finding in the report. */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const severities = new Set(['HIGH', 'CRITICAL'])

export function evaluate(report, policy, image, today = new Date().toISOString().slice(0, 10)) {
  if (report.SchemaVersion !== 2 || !report.Metadata?.ImageID ||
      !Array.isArray(report.Results) || !report.Results.length) {
    throw new Error('Missing or incomplete Trivy image report')
  }
  if (policy.version !== 1 || !Array.isArray(policy.exceptions)) throw new Error('Invalid policy')
  for (const rule of policy.exceptions) {
    if (!rule.reason || !rule.source?.startsWith('https://') ||
        !/^\d{4}-\d{2}-\d{2}$/.test(rule.expires) || !rule.images?.length ||
        !rule.ids?.length || !rule.target || !rule.package || !rule.installed) {
      throw new Error('Incomplete exception: exact scope, expiry and evidence are required')
    }
  }
  const result = { blocked: [], excepted: [], withoutFix: [] }
  for (const target of report.Results) {
    for (const finding of target.Vulnerabilities ?? []) {
      if (!severities.has(finding.Severity)) continue
      if (!finding.VulnerabilityID || !finding.PkgName || !finding.InstalledVersion) {
        throw new Error('Incomplete vulnerability record')
      }
      const item = {
        target: target.Target, id: finding.VulnerabilityID, package: finding.PkgName,
        installed: finding.InstalledVersion, fixed: finding.FixedVersion ?? '',
        severity: finding.Severity,
      }
      if (!finding.FixedVersion) {
        result.withoutFix.push(item)
        continue
      }
      const exception = policy.exceptions.find(rule => {
        if (rule.expires < today || !rule.images.includes(image) || rule.target !== target.Target ||
            rule.package !== finding.PkgName || rule.installed !== finding.InstalledVersion ||
            !rule.ids.includes(finding.VulnerabilityID)) return false
        if (!rule.sbom_only) return true
        const packages = (target.Packages ?? []).filter(p =>
          p.Name === finding.PkgName && p.Version === finding.InstalledVersion)
        // An installed old METADATA entry must never inherit a build-SBOM exception.
        return packages.length > 0 && packages.every(p => p.AnalyzedBy === 'sbom' && !p.FilePath)
      })
      if (exception) result.excepted.push({ ...item, expires: exception.expires, reason: exception.reason })
      else result.blocked.push(item)
    }
  }
  return result
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const [image, file, policyFile = '.github/container-scan-exceptions.json'] = process.argv.slice(2)
    if (!image || !file) throw new Error('Usage: node scripts/check-image-scan.mjs IMAGE REPORT [POLICY]')
    const report = JSON.parse(readFileSync(file, 'utf8'))
    const policy = JSON.parse(readFileSync(policyFile, 'utf8'))
    const result = evaluate(report, policy, image)
    console.log(JSON.stringify({ image, ...result }, null, 2))
    if (result.withoutFix.length) console.warn(`${image}: ${result.withoutFix.length} findings have no vendor fix; review raw report`)
    if (result.blocked.length) {
      console.error(`${image}: ${result.blocked.length} unreviewed HIGH/CRITICAL findings with vendor fixes`)
      process.exitCode = 1
    }
  } catch (error) {
    console.error(error.message)
    process.exitCode = 2
  }
}
