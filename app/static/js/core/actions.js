import { loadAll } from './data.js';
import { state } from './state.js';
import { toast } from '../ui/toast.js';

let acting = false;
export const isActing = () => acting;
function act(work) {
  return async (event) => {
    const button = event?.currentTarget;
    if (button?.disabled || acting) return;
    acting = true;
    if (button) {
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
    }
    try {
      await work();
      if (state.authenticated) await loadAll();
      toast('已完成');
    } catch (error) {
      if (error.message !== 'unauthorized') toast(error.message, true);
    } finally {
      acting = false;
      if (button) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
      }
    }
  };
}

export { act };
