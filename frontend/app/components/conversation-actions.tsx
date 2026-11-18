"use client";

import { useEffect, useRef, useState } from "react";
import { manageConversation } from "@/lib/assistant";
import type { ConversationSummary } from "@/lib/assistant";

export default function ConversationActions({ courseId, conversation, disabled, onRename, onDelete, onBusyChange }: {
  courseId: number; conversation: ConversationSummary; disabled: boolean;
  onRename: (value: ConversationSummary) => void; onDelete: (id: string) => Promise<void>;
  onBusyChange: (value: boolean) => void;
}) {
  const [action, setAction] = useState<"rename" | "delete" | null>(null);
  const [title, setTitle] = useState(conversation.title);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const validTitle = title.trim().length > 0 && title.trim().length <= 100;

  // The in-app dialog uses the browser's focus trap and makes the background inert.
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (action) {
      if (!element.open) element.showModal();
      if (action === "rename") {
        input.current?.focus();
        input.current?.select();
      } else {
        cancel.current?.focus();
      }
    } else if (element.open) {
      element.close();
    }
  }, [action]);

  function open(next: "rename" | "delete") {
    setTitle(conversation.title);
    setError("");
    setAction(next);
  }

  async function save() {
    if (pending || disabled || !action || (action === "rename" && !validTitle)) return;
    setPending(true);
    onBusyChange(true);
    setError("");
    try {
      if (action === "rename") {
        const updated = await manageConversation(courseId, conversation.conversation_id, title);
        if (updated) onRename(updated);
        setAction(null);
      } else {
        await manageConversation(courseId, conversation.conversation_id);
        await onDelete(conversation.conversation_id);
        setAction(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : `Unable to ${action} the conversation.`);
    } finally {
      setPending(false);
      onBusyChange(false);
    }
  }

  return <div className="conversation-actions">
    <div className="conversation-action-buttons" role="group" aria-label="Conversation actions">
      <button className="conversation-action-button" type="button" disabled={disabled || pending}
        onClick={() => open("rename")}>Rename</button>
      <button className="conversation-action-button conversation-action-danger" type="button" disabled={disabled || pending}
        onClick={() => open("delete")}>Delete conversation</button>
    </div>
    <dialog ref={dialog} className="conversation-dialog" aria-labelledby="conversation-dialog-title"
      aria-describedby={action === "delete" ? "conversation-delete-description" : undefined}
      onCancel={event => { event.preventDefault(); if (!pending) setAction(null); }}
      onKeyDown={event => {
        if (event.key !== "Tab") return;
        const controls = event.currentTarget.querySelectorAll<HTMLElement>("input:not(:disabled), button:not(:disabled)");
        const first = controls[0], last = controls[controls.length - 1];
        // Keep Tab within the modal, including while all controls are disabled.
        if (!first) event.preventDefault();
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }}>
      {action && <form onSubmit={event => { event.preventDefault(); void save(); }} aria-busy={pending}>
        <h2 id="conversation-dialog-title">{action === "rename" ? "Rename conversation" : "Delete conversation?"}</h2>
        {action === "rename" ? <div className="conversation-rename">
          <label htmlFor="conversation-name">Conversation name</label>
          <input id="conversation-name" ref={input} value={title} maxLength={100} required disabled={pending || disabled}
            aria-describedby="conversation-name-hint" onChange={event => setTitle(event.target.value)} />
          <p className="assistant-hint" id="conversation-name-hint">Use 1–100 characters.</p>
        </div> : <p id="conversation-delete-description" className="conversation-delete-description">
          &quot;{conversation.title}&quot; will be permanently deleted. Your course materials will not be affected.
        </p>}
        {error && <p className="conversation-dialog-error" role="alert">{error}</p>}
        {pending && <p className="assistant-hint" role="status">{action === "rename" ? "Saving conversation name..." : "Deleting conversation..."}</p>}
        <div className="conversation-dialog-footer">
          <button ref={cancel} className="primary-button" type="button" disabled={pending} onClick={() => setAction(null)}>Cancel</button>
          <button className={action === "delete" ? "primary-button conversation-delete-button" : "primary-button"}
            type="submit" disabled={pending || disabled || (action === "rename" && !validTitle)}>
            {action === "rename" ? "Save" : "Delete"}
          </button>
        </div>
      </form>}
    </dialog>
  </div>;
}
