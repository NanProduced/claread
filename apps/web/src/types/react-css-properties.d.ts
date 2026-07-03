import "react";
import "csstype";

declare module "csstype" {
  interface Properties<TLength = (string & {}) | 0, TTime = string & {}> {
    [key: `--radix-${string}`]: string | number | undefined;
  }
}

declare module "react" {
  interface CSSProperties {
    [key: `--radix-${string}`]: string | number | undefined;
  }
}

declare global {
  namespace React {
    interface CSSProperties {
      [key: `--radix-${string}`]: string | number | undefined;
    }
  }
}
