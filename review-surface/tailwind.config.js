/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#0a0a0a',
        elevated: '#111111',
      },
    },
  },
  plugins: [],
};
