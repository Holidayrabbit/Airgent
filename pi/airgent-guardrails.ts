/**
 * Airgent guardrails extension for the pi agent kernel.
 *
 * Policy-driven tool-call filtering (the "guardrails" pattern): a JSON policy
 * file decides which tool calls are blocked before execution. The policy is
 * rendered to a private path by Airgent at kernel setup time
 * (__AIRGENT_GUARDRAILS_CONFIG__ placeholder).
 *
 * Policy shape (guardrails.json):
 * {
 *   "denied_tools":    ["bash"],                  // tool names to block outright
 *   "denied_commands": ["rm\\s+-rf", "sudo"],     // regexes matched against bash commands
 *   "denied_paths":    ["\\.env$", "secrets/"],   // regexes matched against file paths
 *   "max_bash_timeout": 60,                       // seconds; 0 disables the check
 *   "message": "Blocked by Airgent guardrails policy"
 * }
 */
import { readFileSync } from "node:fs";

const CONFIG_PATH: string = __AIRGENT_GUARDRAILS_CONFIG__;

interface GuardrailsPolicy {
	denied_tools?: string[];
	denied_commands?: string[];
	denied_paths?: string[];
	max_bash_timeout?: number;
	message?: string;
}

let policy: GuardrailsPolicy = {};
try {
	policy = JSON.parse(readFileSync(CONFIG_PATH, "utf8")) as GuardrailsPolicy;
} catch {
	policy = {};
}

function compile(patterns: string[] | undefined): RegExp[] {
	return (patterns ?? [])
		.map((pattern) => {
			try {
				return new RegExp(pattern);
			} catch {
				return null;
			}
		})
		.filter((regex): regex is RegExp => regex !== null);
}

const deniedTools = new Set(policy.denied_tools ?? []);
const deniedCommands = compile(policy.denied_commands);
const deniedPaths = compile(policy.denied_paths);
const maxBashTimeout = policy.max_bash_timeout ?? 0;
const blockMessage = policy.message ?? "Blocked by Airgent guardrails policy";

function blocked(reason: string) {
	return { block: true, reason: `${blockMessage}: ${reason}` };
}

export default function (pi: any) {
	pi.on("tool_call", (event: any) => {
		if (deniedTools.has(event.toolName)) {
			return blocked(`tool '${event.toolName}' is denied`);
		}

		if (event.toolName === "bash" && typeof event.input?.command === "string") {
			for (const regex of deniedCommands) {
				if (regex.test(event.input.command)) {
					return blocked(`command matches denied pattern ${regex.source}`);
				}
			}
			if (maxBashTimeout > 0 && typeof event.input?.timeout === "number" && event.input.timeout > maxBashTimeout) {
				return blocked(`bash timeout ${event.input.timeout}s exceeds limit ${maxBashTimeout}s`);
			}
		}

		const path = event.input?.path ?? event.input?.filePath;
		if (typeof path === "string" && ["read", "write", "edit"].includes(event.toolName)) {
			for (const regex of deniedPaths) {
				if (regex.test(path)) {
					return blocked(`path matches denied pattern ${regex.source}`);
				}
			}
		}

		return undefined;
	});
}
