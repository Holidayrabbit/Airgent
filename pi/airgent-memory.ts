/**
 * Airgent memory extension for the pi agent kernel.
 *
 * Exposes the long-term memory store (the same SQLite database used by the
 * `airgent memory` CLI and the HTTP API) as two model-facing tools:
 *   - search_memory:  keyword search over saved memories
 *   - remember_note:  persist a durable fact for future sessions
 *
 * The extension shells out to `python -m app memory ...` so there is a single
 * source of truth for storage. The Python interpreter path is rendered by
 * Airgent at kernel setup time (the __AIRGENT_PYTHON__ placeholder).
 *
 * Deliberately dependency-free: the parameter schemas are plain JSON Schema
 * objects (what TypeBox produces) and the only import is a Node builtin, so
 * the file loads through pi's jiti loader from the isolated agent directory.
 */
import { execFileSync } from "node:child_process";

const PYTHON: string = __AIRGENT_PYTHON__;

function runMemoryCli(args: string[]): string {
	const result = execFileSync(PYTHON, ["-m", "app", "memory", ...args], {
		encoding: "utf8",
		timeout: 15000,
		windowsHide: true,
		maxBuffer: 4 * 1024 * 1024,
	});
	return result.trim();
}

const searchMemoryParams = {
	type: "object",
	properties: {
		query: { type: "string", description: "Keywords to search for in saved memories" },
		limit: { type: "number", description: "Maximum memories to return (default 8)" },
	},
	required: ["query"],
};

const rememberNoteParams = {
	type: "object",
	properties: {
		content: { type: "string", description: "The durable fact or preference to remember" },
		tags: { type: "string", description: "Optional comma-separated tags" },
	},
	required: ["content"],
};

export default function (pi: any) {
	pi.registerTool({
		name: "search_memory",
		label: "Search memory",
		description:
			"Search the user's long-term memory for stable preferences, ongoing projects, and prior commitments. Use before assuming prior context.",
		parameters: searchMemoryParams,

		async execute(_toolCallId: string, params: any) {
			const limit = Math.max(1, Math.min(50, params.limit ?? 8));
			try {
				const text = runMemoryCli(["search", params.query, "--limit", String(limit), "--format", "json"]);
				return { content: [{ type: "text", text: text || "[]" }], details: undefined };
			} catch (error: any) {
				return { content: [{ type: "text", text: `search_memory failed: ${error?.message ?? error}` }], details: undefined };
			}
		},
	});

	pi.registerTool({
		name: "remember_note",
		label: "Remember note",
		description:
			"Persist a durable fact about the user (preference, project detail, commitment) into long-term memory. Do not store secrets or ephemeral details.",
		parameters: rememberNoteParams,

		async execute(_toolCallId: string, params: any) {
			const args = ["add", params.content];
			if (params.tags) args.push("--tags", params.tags);
			try {
				const text = runMemoryCli(args);
				return { content: [{ type: "text", text: text || "Saved." }], details: undefined };
			} catch (error: any) {
				return { content: [{ type: "text", text: `remember_note failed: ${error?.message ?? error}` }], details: undefined };
			}
		},
	});
}
