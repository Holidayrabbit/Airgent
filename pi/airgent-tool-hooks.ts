/**
 * Airgent tool-level hooks extension for the pi agent kernel.
 *
 * Claude-Code-style PreToolUse/PostToolUse hooks, sharing the same
 * hooks.json as the turn-level (before_run/after_run) Python hooks:
 *
 * {
 *   "hooks": {
 *     "before_run":  [{ "command": "...", "timeout": 10 }],
 *     "after_run":   [{ "command": "...", "timeout": 10 }],
 *     "before_tool": [{ "command": "/path/to/audit.sh", "timeout": 10 }],
 *     "after_tool":  [{ "command": "/path/to/log.sh",  "timeout": 10 }]
 *   }
 * }
 *
 * before_tool hooks run at tool_call time, before execution, with
 * {tool, arguments} as JSON on stdin; exit 2 BLOCKS the tool (stdout is the
 * reason shown to the model). after_tool hooks run at tool_result time with
 * {tool, arguments, content, isError}; their output is ignored.
 *
 * Deployed only when AIRGENT_HOOKS=1 and the config actually declares
 * tool-level hook entries. Path rendered by Airgent at setup time
 * (__AIRGENT_HOOKS_CONFIG__ placeholder).
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const CONFIG_PATH: string = __AIRGENT_HOOKS_CONFIG__;

interface HookEntry {
	command: string;
	timeout?: number;
}

let beforeTool: HookEntry[] = [];
let afterTool: HookEntry[] = [];
try {
	const config = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
	beforeTool = (config?.hooks?.before_tool ?? []).filter((h: any) => h?.command);
	afterTool = (config?.hooks?.after_tool ?? []).filter((h: any) => h?.command);
} catch {
	beforeTool = [];
	afterTool = [];
}

function runHook(entry: HookEntry, payload: unknown): { exitCode: number; stdout: string } | null {
	try {
		const result = execFileSync(entry.command, {
			input: JSON.stringify(payload),
			encoding: "utf8",
			timeout: Math.max(1, entry.timeout ?? 15) * 1000,
			windowsHide: true,
			shell: true,
			maxBuffer: 1024 * 1024,
		});
		return { exitCode: 0, stdout: result.trim() };
	} catch (error: any) {
		if (typeof error?.status === "number") {
			return { exitCode: error.status, stdout: String(error.stdout ?? "").trim() };
		}
		return { exitCode: -1, stdout: String(error?.message ?? error) };
	}
}

export default function (pi: any) {
	if (!beforeTool.length && !afterTool.length) return;

	pi.on("tool_call", async (event: any) => {
		for (const entry of beforeTool) {
			const outcome = runHook(entry, {
				tool: event.toolName,
				arguments: event.input ?? {},
			});
			if (outcome && outcome.exitCode === 2) {
				return {
					block: true,
					reason: `Airgent before_tool hook: ${outcome.stdout || "blocked by hook"}`,
				};
			}
		}
		return undefined;
	});

	pi.on("tool_result", async (event: any) => {
		for (const entry of afterTool) {
			runHook(entry, {
				tool: event.toolName,
				arguments: event.input ?? {},
				content: event.content ?? [],
				isError: Boolean(event.isError),
			});
		}
		return undefined;
	});
}
