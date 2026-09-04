/** True when the SPA runs against the in-memory mock API (`VITE_MOCK=1`). */
export const IS_MOCK: boolean = import.meta.env.VITE_MOCK === '1';
