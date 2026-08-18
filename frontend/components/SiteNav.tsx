"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon, Logo, type IconName } from "@/components/ui/Icon";

/**
 * Primary navigation.
 *
 * The old header gave no indication of where you were — five identically
 * styled links, so the app never told you which section you had opened. It
 * also used an emoji as the logo, which renders differently on every platform
 * and is announced as "movie camera" by screen readers.
 */
const LINKS: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Library", icon: "video" },
  { href: "/ask", label: "Ask", icon: "sparkles" },
  { href: "/search", label: "Search", icon: "search" },
  { href: "/timeline", label: "Timeline", icon: "timeline" },
  { href: "/collections", label: "Collections", icon: "layers" },
];

export function SiteNav() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" || pathname.startsWith("/videos") : pathname.startsWith(href);

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-4 border-b border-line bg-surface/95 px-4 backdrop-blur-sm sm:px-6">
      <Link
        href="/"
        className="flex items-center gap-2 text-accent-400 transition-opacity hover:opacity-80"
      >
        <Logo size={20} />
        <span className="text-md font-semibold tracking-tight text-ink-50">EchoLens</span>
      </Link>

      <nav aria-label="Primary" className="ml-2 min-w-0 flex-1">
        <ul className="flex items-center gap-0.5 overflow-x-auto">
          {LINKS.map((link) => {
            const active = isActive(link.href);
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex h-9 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-sm transition-colors duration-150 ${
                    active
                      ? "bg-ink-800 font-medium text-ink-50"
                      : "text-ink-400 hover:bg-ink-850 hover:text-ink-100"
                  }`}
                >
                  <Icon name={link.icon} size={15} />
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <a
        href="http://localhost:8000/docs"
        target="_blank"
        rel="noreferrer"
        className="hidden items-center gap-1 text-xs text-ink-500 transition-colors hover:text-ink-200 sm:inline-flex"
      >
        API
        <Icon name="external" size={12} />
      </a>
    </header>
  );
}
