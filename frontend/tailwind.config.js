/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'cinema-bg': '#121212',
        'cinema-accent': '#e50914', 
      }
    },
  },
  plugins: [],
}
