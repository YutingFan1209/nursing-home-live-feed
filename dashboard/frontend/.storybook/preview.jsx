import "../src/tokens.css";
import "../src/app.css";

// Each story sits in the site's page wrapper (.nh-wrap: width, gutters) and font
const preview = {
  decorators: [
    (Story) => (
      <div className="nh-wrap" style={{ fontFamily: "var(--font-sans)", color: "var(--color-text)" }}>
        <Story />
      </div>
    ),
  ],
  parameters: { layout: "fullscreen" },
};
export default preview;
