"use client";
import { createElement, useEffect, useRef, useState } from "react";

/**
 * Scroll-triggered reveal. Content is fully visible until hydration; only elements that start below the
 * fold are armed (hidden) and then revealed once. Reduced motion is handled in globals.css, and browsers
 * without IntersectionObserver simply keep the content visible.
 */
export function Reveal({ children, className = "", as = "div", delay = 0 }: { children: React.ReactNode; className?: string; as?: "div" | "section" | "li" | "article"; delay?: number }) {
  const ref = useRef<HTMLElement | null>(null);
  const [armed, setArmed] = useState(false);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const r = el.getBoundingClientRect();
    if (r.top < window.innerHeight * 0.92) return; // already on screen: never hide it
    setArmed(true);
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setShown(true); io.disconnect(); }
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return createElement(as, { ref, className: `reveal ${className}`, "data-armed": armed ? "true" : undefined, "data-in": shown ? "true" : undefined, style: delay ? { transitionDelay: `${delay}ms` } : undefined }, children);
}
