/**
 * Airgent permission extension for the pi agent kernel.
 *
 * Tiered tool permissions, mirroring the mature coding agents:
 *   - "read-only"       block write/edit/bash outright (plan-mode style)
 *   - "workspace-write" file edits confined to the workspace + Airgent data
 *                       dir; bash commands matching ask-patterns trigger an
 *                       interactive confirm; deny-patterns are blocked
 *   - "off"             extension is not deployed at all
 *
 * Interactive approvals ride pi's extension UI protocol: in RPC mode
 * ctx.ui.confirm() emits an `extension_ui_request` on stdout and blocks until
 * Airgent answers with an `extension_ui_response` (the TUI/WebUI surface the
 * question to the user; headless runs auto-deny).
 *
 * Placeholders rendered by Airgent at kernel setup time:
 *   __AIRGENT_PERMISSION_MODE__     "read-only" | "workspace-write"
 *   __AIRGENT_WORKSPACE__           absolute workspace path
 *   __AIRGENT_DATA_DIR__            absolute Airgent data dir
 *   __AIRGENT_PERMISSIONS_CONFIG__  path to the ask/deny pattern JSON
 */
import { readFileSync } from "node:fs";
import { isAbsolute, resolve as resolvePath, sep } from "node:path";

const MODE: string = __AIRGENT_PERMISSION_MODE__;
const WORKSPACE: string = __AIRGENT_WORKSPACE__;
const DATA_DIR: string = __AIRGENT_DATA_DIR__;
const CONFIG_PATH: string = __AIRGENT_PERMISSIONS_CONFIG__;

interface PermissionPolicy {
	ask_commands?: string[];
	deny_commands?: string[];
	deny_tools?: string[];
}

const DEFAULT_ASK_COMMANDS = [
	"sudo\\b",
	"rm\\s+-rf",
	"git\\s+push",
	"git\\s+reset\\s+--hard",
	"npm\\s+(install|i)\\b",
	"pnpm\\s+(install|add)\\b",
	"pip\\s+install",
	"cargo\\s+install",
	"curl[^|]*\\|\\s*(ba)?sh",
	"wget[^|]*\\|\\s*(ba)?sh",
	"docker\\s+(system\\s+prune|volume\\s+(rm|prune))",
];

const DEFAULT_DENY_COMMANDS = [
	"rm\\s+-rf\\s+/(\\s|$)",
	"mkfs(\\.|\\s)",
	"dd\\s+[^;]*of=/dev/[sh]d",
	":\\(\\)\\s*\\{.*\\};\\s*:",
];

const MUTATING_TOOLS = new Set(["write", "edit", "create_file", "edit_file", "bash", "run_bash_command"]);
const WRITE_TOOLS = new Set(["write", "edit", "create_file", "edit_file"]);

let policy: PermissionPolicy = {};
try {
	policy = JSON.parse(readFileSync(CONFIG_PATH, "utf8")) as PermissionPolicy;
} catch {
	policy = {};
}

function compile(patterns: string[] | undefined, fallback: string[]): RegExp[] {
	return (patterns ?? fallback)
		.map((pattern) => {
			try {
				return new RegExp(pattern);
			} catch {
				return null;
			}
		})
		.filter((regex): regex is RegExp => regex !== null);
}

const askCommands = compile(policy.ask_commands, DEFAULT_ASK_COMMANDS);
const denyCommands = compile(policy.deny_commands, DEFAULT_DENY_COMMANDS);
const denyTools = new Set(policy.deny_tools ?? []);

function blocked(reason: string) {
	return { block: true, reason: `Airgent permissions (${MODE}): ${reason}` };
}

function insideRoot(candidate: string, root: string): boolean {
	if (candidate === root) return true;
	return candidate.startsWith(root + sep) || candidate.startsWith(root + "/");
}

/** Resolve a tool path argument against the workspace and normalize it. */
function normalizePath(raw: string): string {
	const path = raw.replace(/\\/g, "/").trim();
	const absolute = isAbsolute(path) ? path : resolvePath(WORKSPACE, path);
	return absolute.replace(/\/+$/, "");
}

export default function (pi: any) {
	pi.on("tool_call", async (event: any, ctx: any) => {
		const tool = event.toolName as string;
		if (denyTools.has(tool)) {
			return blocked(`tool '${tool}' is denied by policy`);
		}

		if (MODE === "read-only") {
			if (MUTATING_TOOLS.has(tool)) {
				return blocked(`read-only mode: '${tool}' is disabled`);
			}
			return undefined;
		}

		if (MODE !== "workspace-write") return undefined;

		if (WRITE_TOOLS.has(tool)) {
			const raw = event.input?.path ?? event.input?.filePath;
			if (typeof raw === "string" && raw.trim()) {
				const resolved = normalizePath(raw);
				if (!insideRoot(resolved, WORKSPACE) && !insideRoot(resolved, DATA_DIR)) {
					return blocked(`write outside the workspace is not allowed: ${raw}`);
				}
			}
			return undefined;
		}

		if (tool === "bash" || tool === "run_bash_command") {
			const command = event.input?.command;
			if (typeof command !== "string" || !command.trim()) return undefined;

			for (const regex of denyCommands) {
				if (regex.test(command)) {
					return blocked(`command matches a denied pattern (${regex.source})`);
				}
			}
			for (const regex of askCommands) {
				if (!regex.test(command)) continue;
				const allowed = await ctx.ui.confirm(
					`Allow command? [${regex.source}]`,
					command.slice(0, 400),
				);
				if (!allowed) {
					return blocked(`user denied: ${command.slice(0, 120)}`);
				}
				return undefined;
			}
		}

		return undefined;
	});
}
