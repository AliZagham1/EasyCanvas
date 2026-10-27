"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Dashboard", path: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z" },
  { href: "/courses", disabled: true, label: "Courses", path: "M12 5v15 M12 5C9 3 5 3 2 4v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Z" },
  { href: "/upcoming", label: "Upcoming", path: "M7 3v4 M17 3v4 M3 10h18 M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z M7 14h3 M7 17h6" },
  { href: "/priorities", disabled: true, label: "Priorities", path: "M5 21V4 M5 4c4-4 10 4 14 0v10c-4 4-10-4-14 0" },
  { href: "/class-schedule", label: "Class Schedule", disabled: true, path: "M7 3v4 M17 3v4 M3 10h18 M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z M8 14h3v3H8z M15 14h2" },
  { href: "/study-assistant", disabled: true, label: "Study Assistant", path: "M21 11a8 8 0 0 1-8 8H7l-5 3V5a2 2 0 0 1 2-2h9a8 8 0 0 1 8 8Z M7 8h9 M7 12h6" },
  { href: "/settings", disabled: true, label: "Settings", path: "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <Link href="/" className="brand">
        <Image
          src="/easycanvas-logo.svg"
          alt=""
          width={32}
          height={32}
          className="shrink-0"
        />
        <span className="brand-name">EasyCanvas</span>
      </Link>
      <nav aria-label="Main navigation" className="sidebar-nav">
        {links.map(({ href, label, path, disabled }) => {
          const content = (
            <>
              <svg className="nav-symbol" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
                <path d={path} />
              </svg>
              {label}
            </>
          );

          return disabled ? (
            <span key={href} className="nav-link" role="link" aria-disabled="true">
              {content}
              <span className="nav-soon">Soon</span>
            </span>
          ) : (
            <Link
              key={href}
              href={href}
              aria-current={pathname === href ? "page" : undefined}
              className={`nav-link ${pathname === href ? "nav-link-active" : ""}`}
            >
              {content}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
