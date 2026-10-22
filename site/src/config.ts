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

/** Any workflow of the repository: its page on GitHub (where "Run workflow" starts it) and its dispatch endpoint. */
export const workflowPage = (file: string) =>
  `https://github.com/${config.owner}/${config.repo}/actions/workflows/${file}`;
export const workflowDispatch = (file: string) =>
  `https://api.github.com/repos/${config.owner}/${config.repo}/actions/workflows/${file}/dispatches`;

/** Where the reset page keeps the owner's fine-grained token (Actions: write on this repository), in this browser only. */
export const TOKEN_KEY = "opt_gh_token";

/** The saved token, or "" when there is none or the browser refuses its storage (Safari in private browsing). */
export function readToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}
