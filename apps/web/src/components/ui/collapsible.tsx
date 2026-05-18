"use client";

import * as React from "react";

type CollapsibleContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
};

const CollapsibleContext = React.createContext<CollapsibleContextValue | null>(null);

function useCollapsibleContext() {
  const context = React.useContext(CollapsibleContext);
  if (!context) {
    throw new Error("Collapsible components must be used inside <Collapsible>.");
  }
  return context;
}

export interface CollapsibleProps extends React.ComponentPropsWithoutRef<"div"> {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}

function Collapsible({
  open,
  defaultOpen = false,
  onOpenChange,
  children,
  ...props
}: CollapsibleProps) {
  const [internalOpen, setInternalOpen] = React.useState(defaultOpen);
  const isControlled = open !== undefined;
  const currentOpen = isControlled ? open : internalOpen;

  const setOpen = React.useCallback(
    (nextOpen: boolean) => {
      if (!isControlled) {
        setInternalOpen(nextOpen);
      }
      onOpenChange?.(nextOpen);
    },
    [isControlled, onOpenChange],
  );

  return (
    <CollapsibleContext.Provider value={{ open: currentOpen, setOpen }}>
      <div data-slot="collapsible" data-state={currentOpen ? "open" : "closed"} {...props}>
        {children}
      </div>
    </CollapsibleContext.Provider>
  );
}

export interface CollapsibleTriggerProps extends React.ComponentPropsWithoutRef<"button"> {
  asChild?: boolean;
}

function CollapsibleTrigger({
  asChild = false,
  children,
  onClick,
  ...props
}: CollapsibleTriggerProps) {
  const { open, setOpen } = useCollapsibleContext();

  const handleClick: React.MouseEventHandler<HTMLButtonElement> = (event) => {
    onClick?.(event);
    if (!event.defaultPrevented) {
      setOpen(!open);
    }
  };

  if (asChild && React.isValidElement(children)) {
    const child = children as React.ReactElement<Record<string, unknown>>;
    return React.cloneElement(child, {
      "data-slot": "collapsible-trigger",
      "data-state": open ? "open" : "closed",
      onClick: (event: React.MouseEvent<HTMLButtonElement>) => {
        const childOnClick = child.props.onClick;
        if (typeof childOnClick === "function") {
          childOnClick(event);
        }
        if (!event.defaultPrevented) {
          setOpen(!open);
        }
      },
    });
  }

  return (
    <button
      type="button"
      data-slot="collapsible-trigger"
      data-state={open ? "open" : "closed"}
      onClick={handleClick}
      {...props}
    >
      {children}
    </button>
  );
}

function CollapsibleContent({
  children,
  hidden,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  const { open } = useCollapsibleContext();

  return (
    <div
      data-slot="collapsible-content"
      data-state={open ? "open" : "closed"}
      hidden={hidden ?? !open}
      {...props}
    >
      {children}
    </div>
  );
}

export { Collapsible, CollapsibleContent, CollapsibleTrigger };
