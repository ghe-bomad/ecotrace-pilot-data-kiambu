# Badges

The DOI and licence badges shown at the top of the [root README](../README.md).

They are committed as static SVG and referenced by relative path rather than
fetched from `shields.io` or `zenodo.org/badge` at render time. Those services
are rate-limited and intermittently unavailable, and a failed fetch renders as a
broken image on GitHub. A committed badge also survives into the Zenodo archive,
where nothing external resolves at all.

| file | shows |
|---|---|
| `doi.svg` | the dataset's concept DOI, `10.5281/zenodo.22232789` |
| `license.svg` | CC BY 4.0 |

`doi.svg` carries the **concept** DOI, which always resolves to the newest
version, so it does not need regenerating at each release. If it ever does need
regenerating, the badges are plain SVG: edit the text and the two `textLength`
attributes, and adjust `width` on the `<svg>` and on the coloured `<rect>` to
match.
