(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.GeneralResizableFrame = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const KEY_STEP = 24;

  function nextHeight(startHeight, deltaY, min, max) {
    const height = Math.round(startHeight + deltaY);
    return Math.min(Math.max(height, min), Math.max(min, max));
  }

  // target の直後に下端ハンドルを置き、ドラッグで高さを変える。ダブルクリックで CSS 既定値へ戻す。
  function attach(target, { min = 80, max = 2400, label = "フレームの高さを調整", onResize } = {}) {
    const handle = document.createElement("div");
    handle.className = "resize-handle";
    handle.tabIndex = 0;
    handle.title = "ドラッグで高さを調整（ダブルクリックで元に戻す）";
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "horizontal");
    handle.setAttribute("aria-label", label);

    let frame = 0;
    const apply = (height) => {
      target.style.height = height === null ? "" : `${height}px`;
      target.style.maxHeight = height === null ? "" : "none";
      target.classList.toggle("user-sized", height !== null);
      if (!onResize || frame) return;
      const run = () => { frame = 0; onResize(); };
      if (typeof requestAnimationFrame === "function") frame = requestAnimationFrame(run); else run();
    };
    const currentHeight = () => target.getBoundingClientRect().height;

    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const startY = event.clientY;
      const startHeight = currentHeight();
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
      document.body.classList.add("resizing-frame");
      const move = (moveEvent) => apply(nextHeight(startHeight, moveEvent.clientY - startY, min, max));
      const end = () => {
        handle.classList.remove("dragging");
        document.body.classList.remove("resizing-frame");
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", end);
        handle.removeEventListener("pointercancel", end);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", end);
      handle.addEventListener("pointercancel", end);
    });
    handle.addEventListener("dblclick", () => apply(null));
    handle.addEventListener("keydown", (event) => {
      const delta = event.key === "ArrowDown" ? KEY_STEP : event.key === "ArrowUp" ? -KEY_STEP : 0;
      if (!delta) return;
      event.preventDefault();
      apply(nextHeight(currentHeight(), delta, min, max));
    });

    target.after(handle);
    return handle;
  }

  return { attach, nextHeight };
});
