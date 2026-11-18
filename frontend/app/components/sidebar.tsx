"use client";

import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";

type NavLink = { href: string; label: string; path: string; disabled?: boolean; secondary?: boolean };

const links: NavLink[] = [
  { href: "/", label: "Dashboard", path: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z" },
  { href: "/courses", label: "Courses", path: "M12 5v15 M12 5C9 3 5 3 2 4v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Z" },
  { href: "/upcoming", label: "Upcoming", path: "M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01" },
  { href: "/priorities", label: "Priorities", path: "M5 21V4 M5 4c4-4 10 4 14 0v10c-4 4-10-4-14 0" },
  { href: "/class-schedule", label: "Class Schedule", path: "M7 3v4 M17 3v4 M3 10h18 M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z M8 14h3v3H8z M15 14h2" },
  { href: "/study-assistant", label: "Study Assistant", path: "M21 11a8 8 0 0 1-8 8H7l-5 3V5a2 2 0 0 1 2-2h9a8 8 0 0 1 8 8Z M7 8h9 M7 12h6" },
  { href: "/settings", label: "Settings", path: "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6", secondary: true },
];

const menuIcon = "M4 6h16 M4 12h16 M4 18h16";
const closeIcon = "M6 6l12 12 M18 6L6 18";

/**
 * Desktop: a fixed sidebar. Phones: a top bar with a Menu button that opens the full list, so every page is reachable.
 * The menu is tied to the path it was opened on, so navigating (links, back/forward) always closes it.
 */
export default function Sidebar() {
  const pathname = usePathname();
  const [openPath, setOpenPath] = useState<string | null>(null);
  const open = openPath === pathname;
  const button = useRef<HTMLButtonElement>(null);
  const close = () => setOpenPath(null);

  function keyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && open) { close(); button.current?.focus(); }
  }

  return (
    <aside className="sidebar" data-open={open} onKeyDown={keyDown}>
      <div className="sidebar-bar">
        <Link href="/" className="brand">
          <Image src="/easycanvas-logo.svg" alt="" width={32} height={32} className="shrink-0" />
          <span className="brand-name">EasyCanvas</span>
        </Link>
        <button ref={button} type="button" className="menu-button" aria-expanded={open} aria-controls="sidebar-panel"
          onClick={() => setOpenPath(open ? null : pathname)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d={open ? closeIcon : menuIcon} />
          </svg>
          {open ? "Close" : "Menu"}
        </button>
      </div>
      {open && <div className="sidebar-scrim" onClick={close} aria-hidden="true" />}
      <div id="sidebar-panel" className="sidebar-panel">
        <nav aria-label="Main navigation" className="sidebar-nav">
          {links.map(({ href, label, path, disabled, secondary }) => {
            const content = (
              <>
                <svg className="nav-symbol" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
                  <path d={path} />
                </svg>
                {label}
              </>
            );
            const spacing = secondary ? " nav-link-secondary" : "";

            return disabled ? (
              <span key={href} className={`nav-link${spacing}`} role="link" aria-disabled="true">
                {content}
                <span className="nav-soon">Soon</span>
              </span>
            ) : (
              <Link
                key={href}
                href={href}
                aria-current={pathname === href ? "page" : undefined}
                onClick={close}
                className={`nav-link${spacing} ${pathname === href || (href !== "/" && pathname.startsWith(`${href}/`)) ? "nav-link-active" : ""}`}
              >
                {content}
              </Link>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}
