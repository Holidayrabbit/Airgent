/**
 * Airgent sub-agent extension for the pi agent kernel.
 *
 * Exposes a `spawn_subagent` tool so the root agent can delegate focused
 * subtasks (the "subagents / multi-agent" pattern): each call spawns a fresh
 * headless `pi --mode rpc` process, runs the task to completion, and returns
 * the sub-agent's final text. Sub-agents carry AIRGENT_SUBAGENT_DEPTH in
 * their environment; when the depth limit is reached the tool refuses, so
 * delegation trees terminate.
 *
 * Placeholders rendered by Airgent at kernel setup time:
 *   __AIRGENT_PI_BIN__            absolute pi binary
 *   __AIRGENT_SUBAGENT_MODEL__    JSON string: model id or null
 *   __AIRGENT_SUBAGENT_DEPTH__    max delegation depth (1 = root only)
 */
import { spawn } from "node:child_process";

const PI_BIN: string = __AIRGENT_PI_BIN__;
const SUBAGENT_MODEL: string | null = __AIRGENT_SUBAGENT_MODEL__;
const MAX_DEPTH: number = __AIRGENT_SUBAGENT_DEPTH__;

interface SubagentOutcome {
	text: string;
	isError: boolean;
}

function runSubagent(task: string, timeoutMs: number): Promise<SubagentOutcome> {
	return new Promise((resolve) => {
		const argv = [PI_BIN, "--mode", "rpc", "--no-session"];
		if (SUBAGENT_MODEL) {
			argv.push("--model", SUBAGENT_MODEL);
		}
		const child = spawn(argv[0], argv.slice(1), {
			stdio: ["pipe", "pipe", "pipe"],
			env: { ...process.env, AIRGENT_SUBAGENT_DEPTH: "1" },
			windowsHide: true,
		});

		let buffer = "";
		let lastText = "";
		let settled = false;

		const finish = (outcome: SubagentOutcome) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			try {
				child.kill();
			} catch {}
			resolve(outcome);
		};

		const timer = setTimeout(() => {
			finish({ text: lastText || "sub-agent timed out", isError: true });
		}, timeoutMs);

		child.stdout!.on("data", (chunk: Buffer) => {
			buffer += chunk.toString("utf8");
			let newline = buffer.indexOf("\n");
			while (newline !== -1) {
				const line = buffer.slice(0, newline).trim();
				buffer = buffer.slice(newline + 1);
				newline = buffer.indexOf("\n");
				if (!line) continue;
				let record: any;
				try {
					record = JSON.parse(line);
				} catch {
					continue;
				}
				if (record?.type === "message_update") {
					const delta = record.assistantMessageEvent ?? {};
					if (delta.type === "text_delta" && typeof delta.delta === "string") {
						lastText += delta.delta;
					}
					if (delta.type === "text_end" && typeof delta.content === "string") {
						lastText = delta.content;
					}
				} else if (record?.type === "agent_end") {
					const messages: any[] = Array.isArray(record.messages) ? record.messages : [];
					for (let i = messages.length - 1; i >= 0; i--) {
						const message = messages[i];
						if (message?.role !== "assistant") continue;
						const text = (message.content ?? [])
							.filter((block: any) => block?.type === "text" && typeof block.text === "string")
							.map((block: any) => block.text)
							.join("\n")
							.trim();
						if (text) {
							finish({ text, isError: false });
							return;
						}
					}
					finish({ text: lastText, isError: false });
					return;
				}
			}
		});

		child.on("error", (error: Error) => {
			finish({ text: `sub-agent failed to start: ${error.message}`, isError: true });
		});
		child.on("exit", () => {
			finish({ text: lastText, isError: false });
		});

		child.stdin!.write(JSON.stringify({ type: "prompt", message: task }) + "\n");
	});
}

const spawnSubagentParams = {
	type: "object",
	properties: {
		task: { type: "string", description: "Self-contained task for the sub-agent. Include all context it needs." },
		timeout_seconds: { type: "number", description: "Optional timeout in seconds (default 300, max 900)." },
	},
	required: ["task"],
};

export default function (pi: any) {
	pi.registerTool({
		name: "spawn_subagent",
		label: "Spawn sub-agent",
		description:
			"Delegate a self-contained subtask to a fresh headless agent and return its final answer. " +
			"Use for parallelizable, well-scoped work (research, refactors, bulk edits). " +
			"The sub-agent has no memory of this conversation; put everything it needs into `task`.",
		parameters: spawnSubagentParams,

		async execute(_toolCallId: string, params: any) {
			const currentDepth = Number(process.env.AIRGENT_SUBAGENT_DEPTH ?? "0");
			if (currentDepth >= MAX_DEPTH) {
				return {
					content: [{ type: "text", text: `spawn_subagent refused: max delegation depth ${MAX_DEPTH} reached.` }],
					details: undefined,
				};
			}
			const timeoutMs = Math.max(10, Math.min(900, params.timeout_seconds ?? 300)) * 1000;
			try {
				const outcome = await runSubagent(String(params.task), timeoutMs);
				return { content: [{ type: "text", text: outcome.text || "(sub-agent returned no text)" }], details: undefined };
			} catch (error: any) {
				return { content: [{ type: "text", text: `spawn_subagent failed: ${error?.message ?? error}` }], details: undefined };
			}
		},
	});
}
