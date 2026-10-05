# Workspace brand

Use README.md as the brand source: Plata means Play, Teach, Along, and its
philosophy is “Play comes first. Learning comes along.”

Replace the generic sprout in the React workspace with the existing
`assets/brand/plata-icon.png`. Import that source directly so the website and
README share one asset; use it for the browser favicon too. Keep its aspect ratio
and transparent background. Use “Plata” for the wordmark, “Play. Teach. Along.”
for the compact caption, and the philosophy in the sidebar footer. Preserve
accessible link naming and verify the production build and responsive rendering.

## iPhone home-screen bookmarks

Declare `apple-touch-icon` explicitly using the same PNG asset, and set
`apple-mobile-web-app-title` to Plata. The ordinary favicon alone does not
provide the explicit iOS home-screen icon metadata. Existing saved shortcuts
may need to be removed and added again after refreshing the page.

Reference: [Apple Web Clip icon guidance](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariWebContent/ConfiguringWebApplications/ConfiguringWebApplications.html).
