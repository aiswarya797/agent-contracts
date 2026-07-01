const { spawnSync } = require("node:child_process");

const MINIMUM_MAJOR = 3;
const MINIMUM_MINOR = 10;

function candidateSpecs() {
  const named = ["python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"].map((command) => ({
    command,
    args: [],
    label: command,
  }));
  if (process.platform !== "win32") {
    return named;
  }
  const pyLauncher = ["-3.13", "-3.12", "-3.11", "-3.10"].map((version) => ({
    command: "py",
    args: [version],
    label: `py ${version}`,
  }));
  return [...named, ...pyLauncher];
}

function parseVersion(output) {
  const match = String(output).trim().match(/^(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!match) {
    return null;
  }
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3] || 0),
  };
}

function isSupportedVersion(version) {
  if (!version) {
    return false;
  }
  return version.major > MINIMUM_MAJOR || (version.major === MINIMUM_MAJOR && version.minor >= MINIMUM_MINOR);
}

function probeCandidate(candidate) {
  const probe = spawnSync(
    candidate.command,
    [
      ...candidate.args,
      "-c",
      "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')",
    ],
    { encoding: "utf8" },
  );
  if (probe.error && probe.error.code === "ENOENT") {
    return { ok: false, reason: "not-found" };
  }
  if (probe.error) {
    return { ok: false, reason: probe.error.message };
  }
  if (probe.status !== 0) {
    return { ok: false, reason: `probe-exited-${probe.status}` };
  }
  const version = parseVersion(probe.stdout);
  if (!isSupportedVersion(version)) {
    return {
      ok: false,
      reason: version ? `unsupported-${version.major}.${version.minor}.${version.patch}` : "unparseable-version",
    };
  }
  return { ok: true, version };
}

function findSupportedPython() {
  const attempted = [];
  for (const candidate of candidateSpecs()) {
    const probe = probeCandidate(candidate);
    attempted.push({ label: candidate.label, reason: probe.reason || "supported" });
    if (probe.ok) {
      return { candidate, version: probe.version, attempted };
    }
  }
  return { candidate: null, version: null, attempted };
}

function runPython(args, options = {}) {
  const toolName = options.toolName || "agent-contracts";
  const selection = findSupportedPython();
  if (!selection.candidate) {
    const tried = selection.attempted.map((item) => `${item.label} (${item.reason})`).join(", ");
    console.error(`${toolName} requires Python 3.10+ on PATH. Tried: ${tried || "no candidates"}`);
    return 1;
  }

  const result = spawnSync(selection.candidate.command, [...selection.candidate.args, ...args], { stdio: "inherit" });
  if (result.error) {
    console.error(result.error.message);
    return 1;
  }
  return result.status ?? 1;
}

module.exports = {
  candidateSpecs,
  findSupportedPython,
  isSupportedVersion,
  parseVersion,
  runPython,
};
