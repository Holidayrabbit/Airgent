/**
 * Airgent MCP bridge extension for the pi agent kernel.
 *
 * pi intentionally ships without MCP support; this extension adds it:
 * it reads an MCP server config (Claude-Desktop-compatible `mcpServers`
 * shape), spawns each stdio server, performs the JSON-RPC handshake
 * (initialize -> notifications/initialized -> tools/list), and registers
 * every remote tool as a pi tool named `mcp__<server>__<tool>` that proxies
 * `tools/call`.
 *
 * Config path is rendered by Airgent at kernel setup time
 * (__AIRGENT_MCP_CONFIG__ placeholder). Config shape:
 * {
 *   "mcpServers": {
 *     "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"] }
 *   }
 * }
 */
import { spawn, type ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";

const CONFIG_PATH: string = __AIRGENT_MCP_CONFIG__;
const PROTOCOL_VERSION = "2024-11-05";
const STARTUP_TIMEOUT_MS = 15000;
const CALL_TIMEOUT_MS = 120000;

interface JsonRpcResponse {
	id: number;
	result?: any;
	error?: { message: string };
}

class McpClient {
	private nextId = 0;
	private pending = new Map<number, (response: JsonRpcResponse) => void>();
	private buffer = "";
	private child: ChildProcess | null = null;

	constructor(
		private readonly serverName: string,
		private readonly command: string,
		private readonly args: string[],
		private readonly env: Record<string, string> | undefined,
	) {}

	async start(): Promise<void> {
		this.child = spawn(this.command, this.args, {
			stdio: ["pipe", "pipe", "pipe"],
			env: { ...process.env, ...(this.env ?? {}) },
			windowsHide: true,
		});
		this.child.stdout!.on("data", (chunk: Buffer) => this.onData(chunk));
		this.child.stderr!.on("data", () => {/* diagnostics ignored */});
		this.child.on("exit", () => {
			for (const pending of this.pending.values()) {
				pending({ id: 0, error: { message: `MCP server '${this.serverName}' exited` } });
			}
			this.pending.clear();
		});

		const initialized = await this.request("initialize", {
			protocolVersion: PROTOCOL_VERSION,
			capabilities: {},
			clientInfo: { name: "airgent-mcp", version: "0.1.0" },
		});
		if (!initialized.result) {
			throw new Error(initialized.error?.message ?? "initialize failed");
		}
		this.notify("notifications/initialized", {});
	}

	private onData(chunk: Buffer) {
		this.buffer += chunk.toString("utf8");
		let newline = this.buffer.indexOf("\n");
		while (newline !== -1) {
			const line = this.buffer.slice(0, newline).trim();
			this.buffer = this.buffer.slice(newline + 1);
			newline = this.buffer.indexOf("\n");
			if (!line) continue;
			let record: any;
			try {
				record = JSON.parse(line);
			} catch {
				continue;
			}
			if (typeof record?.id === "number" && this.pending.has(record.id)) {
				const resolve = this.pending.get(record.id)!;
				this.pending.delete(record.id);
				resolve(record as JsonRpcResponse);
			}
		}
	}

	private send(record: any) {
		this.child!.stdin!.write(JSON.stringify(record) + "\n");
	}

	private notify(method: string, params: any) {
		this.send({ jsonrpc: "2.0", method, params });
	}

	private request(method: string, params: any, timeoutMs = STARTUP_TIMEOUT_MS): Promise<JsonRpcResponse> {
		const id = ++this.nextId;
		return new Promise((resolve, reject) => {
			const timer = setTimeout(() => {
				this.pending.delete(id);
				reject(new Error(`MCP '${this.serverName}' ${method} timed out`));
			}, timeoutMs);
			this.pending.set(id, (response) => {
				clearTimeout(timer);
				resolve(response);
			});
			this.send({ jsonrpc: "2.0", id, method, params });
		});
	}

	async listTools(): Promise<any[]> {
		const response = await this.request("tools/list", {}, STARTUP_TIMEOUT_MS);
		return Array.isArray(response.result?.tools) ? response.result.tools : [];
	}

	async callTool(name: string, args: any): Promise<string> {
		const response = await this.request("tools/call", { name, arguments: args ?? {} }, CALL_TIMEOUT_MS);
		if (response.error) {
			return `MCP tool error: ${response.error.message}`;
		}
		const content = response.result?.content;
		if (Array.isArray(content)) {
			return content
				.filter((block: any) => block?.type === "text" && typeof block.text === "string")
				.map((block: any) => block.text)
				.join("\n")
				.trim();
		}
		return JSON.stringify(response.result ?? {});
	}
}

async function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
	let timer: ReturnType<typeof setTimeout> | undefined;
	const timeout = new Promise<never>((_, reject) => {
		timer = setTimeout(() => reject(new Error(`${label} timed out`)), ms);
	});
	try {
		return await Promise.race([promise, timeout]);
	} finally {
		if (timer) clearTimeout(timer);
	}
}

export default function (pi: any) {
	const config = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
	const servers = config?.mcpServers ?? {};
	for (const [serverName, serverConfig] of Object.entries(servers)) {
		const spec = serverConfig as { command: string; args?: string[]; env?: Record<string, string> };
		if (!spec?.command) continue;

		const client = new McpClient(serverName, spec.command, spec.args ?? [], spec.env);
		withTimeout(client.start(), STARTUP_TIMEOUT_MS, `MCP server '${serverName}' start`)
			.then(() => client.listTools())
			.then((tools) => {
				for (const tool of tools) {
					if (!tool?.name) continue;
					pi.registerTool({
						name: `mcp__${serverName}__${tool.name}`,
						label: tool.name,
						description: tool.description
							? `[mcp:${serverName}] ${tool.description}`
							: `[mcp:${serverName}] ${tool.name}`,
						parameters: tool.inputSchema ?? { type: "object", properties: {} },
						async execute(_toolCallId: string, params: any) {
							try {
								const text = await client.callTool(tool.name, params);
								return { content: [{ type: "text", text: text || "(empty result)" }], details: undefined };
							} catch (error: any) {
								return {
									content: [{ type: "text", text: `mcp__${serverName}__${tool.name} failed: ${error?.message ?? error}` }],
									details: undefined,
								};
							}
						},
					});
				}
			})
			.catch((error: Error) => {
				// Surface the failure through the kernel's extension error channel.
				throw new Error(`Airgent MCP bridge: server '${serverName}' failed: ${error.message}`);
			});
	}
}
