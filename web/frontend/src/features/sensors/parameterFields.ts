import type { TFunction } from 'i18next'

/** The label of a field: its translation if there is one, else its own name.
 *
 * Shared between the parameter card and the variant dialog - both used to
 * carry byte-identical private copies, which is how the same description
 * ended up rendered three times on one page.
 */
export function fieldLabel(
  t: TFunction,
  field: { name: string; label_key?: string },
) {
  return field.label_key ? t(field.label_key, field.name) : field.name
}

/** The description of a field.
 *
 * The English plain text ships with the sensor and is kept in step with the
 * script's own argparse help by tests/sensor-checks.py. A translation key is
 * optional on top - a reference that shows nothing until every sensor is
 * translated would be a reference nobody can use yet.
 */
export function fieldDescription(
  t: TFunction,
  field: { description?: string; description_key?: string },
) {
  if (field.description_key) return t(field.description_key, field.description ?? '')
  return field.description ?? ''
}
