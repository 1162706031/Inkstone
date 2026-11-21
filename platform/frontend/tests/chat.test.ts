import { afterEach, expect, it, vi } from "vitest";
import { applyMessageEvent, subscribe } from "../src/workbench";
import type { Contract } from "../src/api/client";

afterEach(() => vi.unstubAllGlobals());

it("resumes from the snapshot watermark, deduplicates deltas and replaces partial snapshots", async () => {
  let messages: Contract["Message"][] = [
    {
      id: "out",
      conversation_id: "conv",
      run_id: "root",
      sequence: 2,
      role: "assistant",
      content: [{ type: "text", block_id: "block", text: "甲" }],
      status: "streaming",
      created_at: "2026-01-01T00:00:00Z",
    },
  ];
  function frame(sequence: number, type: string, payload: object, root = "root") {
    const data = {
      sequence,
      type,
      payload,
      schema_version: 1,
      root_run_id: root,
      message_id: "out",
      block_id: "block",
    };
    return `id: ${root}:${sequence}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
  }
  const delta = frame(5, "message.delta", { text: "乙" });
  const snapshot = frame(9, "message.snapshot", {
    content: [{ type: "text", block_id: "block", text: "甲乙丙" }],
    status: "cancelled",
  });
  const stream =
    frame(3, "message.delta", { text: "甲" }) +
    delta +
    delta +
    frame(6, "message.delta", { text: "错" }, "other") +
    snapshot;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream)));
  const history: string[] = [];
  await subscribe("root", 3, new AbortController().signal, (e) => {
    messages = applyMessageEvent(messages, e);
    history.push(messages[0].content[0].text);
  });
  expect(history).toEqual(["甲乙", "甲乙丙"]);
  expect(messages[0].status).toBe("cancelled");
});
