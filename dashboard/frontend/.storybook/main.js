// Storybook for the live feed's card component (Vite builder). Output goes to
// storybook-static/, which is gitignored and never part of the deployed build.
const config = {
  stories: ["../src/**/*.stories.jsx"],
  framework: { name: "@storybook/react-vite", options: {} },
  core: { disableTelemetry: true },
  // The site's own index.html loads Public Sans from Google Fonts; the stories do the same
  previewHead: (head) => `${head}
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Public+Sans:ital,wght@0,400;0,600;0,700;1,400&display=swap" rel="stylesheet" />`,
};
export default config;
