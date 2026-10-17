// Only lifecycle signals cross feature boundaries; page modules never import the router.
export const events = new EventTarget();
export function emit(name) {
  events.dispatchEvent(new Event(name));
}
