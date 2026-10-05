/** Where the dashboard reads its data from. Overridable at build time with Vite env variables. */
const env = import.meta.env as Record<string, string | undefined>;

export const config = {
  owner: env.VITE_OWNER ?? "andreramolivaz",
  repo: env.VITE_REPO ?? "oil-paper-trading",
  dataBranch: env.VITE_DATA_BRANCH ?? "data",
  dataDir: env.VITE_DATA_DIR ?? "site-data",
  workflowFile: "reset.yml",
  /** How old the engine's output may be before the page flags it. */
  staleWarnHours: 2,
  staleErrorHours: 24,
  fetchTimeoutMs: 8000,
} as const;

export const rawBaseUrl = () =>
  `https://raw.githubusercontent.com/${config.owner}/${config.repo}/${config.dataBranch}/${config.dataDir}`;

export const workflowUrl = () =>
  `https://github.com/${config.owner}/${config.repo}/actions/workflows/${config.workflowFile}`;

export const dispatchUrl = () =>
  `https://api.github.com/repos/${config.owner}/${config.repo}/actions/workflows/${config.workflowFile}/dispatches`;

export const repoUrl = () => `https://github.com/${config.owner}/${config.repo}`;
