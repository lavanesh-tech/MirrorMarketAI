"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import {
  useAddComment,
  useComments,
  useDeleteComment,
  useMe,
  useWorkspace,
  useWorkspaceProducts,
} from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format";
import { commentSchema } from "@/lib/validation";

/** Comments on the decision. New ones from other members appear by themselves. */
export function Discussion({ workspaceId }: { workspaceId: string }) {
  const comments = useComments(workspaceId);
  const products = useWorkspaceProducts(workspaceId);
  const workspace = useWorkspace(workspaceId);
  const me = useMe();
  const add = useAddComment(workspaceId);
  const remove = useDeleteComment(workspaceId);
  const [error, setError] = useState<string | null>(null);

  const role = workspace.data?.my_role;
  const canComment = role !== undefined && role !== "VIEWER";
  const productName = (id: string | null) => {
    const found = products.data?.find((item) => item.product.id === id)?.product;
    return found ? `${found.brand} ${found.name}` : null;
  };

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const parsed = commentSchema.safeParse({ body: data.get("body") });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check the comment.");
      return;
    }
    setError(null);
    try {
      const about = String(data.get("product_id") ?? "");
      await add.mutateAsync({ body: parsed.data.body, productId: about === "" ? null : about });
      form.reset();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The comment could not be posted.");
    }
  }

  async function onDelete(commentId: string) {
    setError(null);
    try {
      await remove.mutateAsync(commentId);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The comment could not be deleted.");
    }
  }

  return (
    <section aria-labelledby="discussion-title">
      <h2 id="discussion-title" className="mb-3 text-xl">
        Discussion
      </h2>
      {comments.isPending ? (
        <p className="text-muted">Loading comments…</p>
      ) : comments.isError ? (
        <p role="alert">Comments could not be loaded.</p>
      ) : comments.data.length === 0 ? (
        <p className="max-w-xl text-muted">No comments yet.</p>
      ) : (
        <ol className="max-w-2xl divide-y divide-line border-y border-line">
          {comments.data.map((comment) => {
            const about = productName(comment.product_id);
            const mine = comment.author_id === me.data?.id;
            return (
              <li key={comment.id} className="py-3">
                <p className="text-sm text-muted">
                  <span className="font-semibold text-ink">{comment.author_name}</span>
                  {about ? ` on ${about}` : ""} · {formatDateTime(comment.created_at)}
                  {comment.edited_at ? " · edited" : ""}
                </p>
                {comment.deleted ? (
                  <p className="mt-1 text-muted italic">This comment was deleted.</p>
                ) : (
                  <p className="mt-1 whitespace-pre-wrap">{comment.body}</p>
                )}
                {!comment.deleted && (mine || role === "OWNER") ? (
                  <button
                    type="button"
                    className="link mt-1 text-sm"
                    aria-label={`Delete comment by ${comment.author_name}`}
                    disabled={remove.isPending}
                    onClick={() => onDelete(comment.id)}
                  >
                    Delete
                  </button>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      {canComment ? (
        <form onSubmit={onSubmit} noValidate className="mt-4 max-w-2xl">
          <label htmlFor="comment-body" className="block font-semibold">
            Add a comment
          </label>
          <textarea
            id="comment-body"
            name="body"
            className="field mt-1 min-h-20"
            maxLength={4000}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "comment-error" : undefined}
          />
          <div className="mt-2 flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor="comment-product" className="block text-sm text-muted">
                About
              </label>
              <select
                id="comment-product"
                name="product_id"
                className="field py-1.5"
                defaultValue=""
              >
                <option value="">The whole decision</option>
                {(products.data ?? []).map(({ product }) => (
                  <option key={product.id} value={product.id}>
                    {product.brand} {product.name}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="button" disabled={add.isPending}>
              {add.isPending ? "Posting…" : "Post comment"}
            </button>
          </div>
          {error ? (
            <p id="comment-error" role="alert" className="mt-2 text-sm font-medium text-unmet">
              {error}
            </p>
          ) : null}
        </form>
      ) : null}
    </section>
  );
}
