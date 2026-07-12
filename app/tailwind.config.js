/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        review: "#dc2626",
        standard: "#f59e0b",
        highconf: "#16a34a",
        gray: {
          850: "#1a1a1a",
        },
      },
    },
  },
  plugins: [],
};