import type { ReactNode, CSSProperties } from "react";

/** Container that applies CSS stagger delays to children */
export function StaggerChildren({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={`stagger-in ${className ?? ""}`} style={style}>
      {children}
    </div>
  );
}

/** Individual stagger child — CSS animation with nth-child delay */
export function StaggerItem({
  children,
  className,
  style,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  onClick?: () => void;
}) {
  return (
    <div className={`stagger-item ${className ?? ""}`} style={style} onClick={onClick}>
      {children}
    </div>
  );
}

/** Collapsible — CSS grid-template-rows trick */
export function CollapsibleContent({
  open,
  children,
  className,
}: {
  open: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`collapse-wrap${open ? " open" : ""} ${className ?? ""}`}>
      <div className="collapse-inner">
        {children}
      </div>
    </div>
  );
}

/** Page wrapper — CSS fade-up on mount */
export function PageTransition({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`page-enter ${className ?? ""}`}>
      {children}
    </div>
  );
}

/** Scale-fade popup — CSS only */
export function PopIn({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={`pop-in ${className ?? ""}`} style={style}>
      {children}
    </div>
  );
}
