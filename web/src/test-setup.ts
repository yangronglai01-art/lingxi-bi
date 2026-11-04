// Headless SVG text measurement only; this is not browser visual acceptance.
if (typeof HTMLCanvasElement !== "undefined") {
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    value: () => ({ measureText: (text: string) => ({ width: text.length * 8 }) }),
  });
}
