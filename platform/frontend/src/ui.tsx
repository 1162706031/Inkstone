import { Alert, Button, Space, Typography } from "antd";
import type { ReactNode } from "react";
import { api } from "./api/client";

export type Page<T> = { items: T[]; next_cursor: string | null };

export const errorText = (e: unknown) => (e instanceof Error ? e.message : "请求失败");

/** Fetch every page of a cursor-paginated list. */
export async function all<T>(path: string) {
  const items: T[] = [];
  let cursor: string | null = null;
  do {
    const page: Page<T> = await api(
      path + (cursor ? `?limit=100&cursor=${encodeURIComponent(cursor)}` : "?limit=100"),
    );
    items.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return items;
}

export function withCursor(path: string, cursor: string | null) {
  return cursor ? `${path}?cursor=${encodeURIComponent(cursor)}` : path;
}

const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
});

export function formatTime(iso?: string | null) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : timeFormat.format(date);
}

export function PageHeader({ title, description, extra, back }: {
  title: ReactNode; description?: ReactNode; extra?: ReactNode; back?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div className="page-header-main">
        {back}
        <Typography.Title level={3} className="page-title">{title}</Typography.Title>
        {description && <Typography.Text type="secondary">{description}</Typography.Text>}
      </div>
      {extra && <Space wrap>{extra}</Space>}
    </div>
  );
}

/** Standard page container: padding, max width, header. */
export function PageBody({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return <div className={wide ? "page page-wide" : "page"}>{children}</div>;
}

export function ErrorAlert({ error }: { error?: unknown }) {
  return error ? <Alert type="error" showIcon title={errorText(error)} /> : null;
}

export function PageButtons({ cursor, next, set }: {
  cursor: string | null; next?: string | null; set: (value: string | null) => void;
}) {
  if (!cursor && !next) return null;
  return (
    <Space className="page-buttons">
      {cursor && <Button onClick={() => set(null)}>首页</Button>}
      {next && <Button onClick={() => set(next)}>下一页</Button>}
    </Space>
  );
}

