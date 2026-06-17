import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',   // format.ts is pure — no DOM needed
    include:     ['tests/**/*.test.ts'],
    globals:     false,
  },
});
