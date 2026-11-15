# Website fonts

- **Satoshi Variable**, normal weights 300–900: obtained unmodified from the
  [official Fontshare download](https://api.fontshare.com/v2/fonts/download/satoshi).
  See [Satoshi](https://www.fontshare.com/fonts/satoshi) and `Satoshi-FFL.txt`.
  The ITF Free Font License allows self-hosting on the project's own website
  and limits redistribution. The binary is intentionally ignored by Git.
  `npm run fonts` in `frontend` obtains the pinned official file. No font
  conversion, subsetting, renaming of the font family, or installer is used.
- **DM Sans**, normal weights 400–700, Latin and extended Latin WOFF2 subsets:
  distributed by [Google Fonts](https://fonts.google.com/specimen/DM+Sans),
  from the [DM Sans project](https://github.com/googlefonts/dm-fonts), under
  the SIL Open Font License in `DM-Sans-OFL.txt`.
  The files are the unmodified Google Fonts webfont responses from
  `https://fonts.googleapis.com/css2?family=DM+Sans:wght@400..700&display=swap`.

All browser font requests are same-origin. No visitor font request is sent
to Fontshare or Google. Setup needs network access only when the verified
local Satoshi file is absent or has a different checksum.
