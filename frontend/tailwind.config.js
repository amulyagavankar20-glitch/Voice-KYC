/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eef7ff',
          100: '#d9ecff',
          200: '#bcdeff',
          300: '#8ecaff',
          400: '#59adff',
          500: '#338bff',
          600: '#1a6af5',
          700: '#1354e1',
          800: '#1644b6',
          900: '#183c8f',
        }
      }
    },
  },
  plugins: [],
}
