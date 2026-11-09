import i18n, { type TFunction } from 'i18next'

/**
 * Human names and help for the fields a sensor declares.
 *
 * The parameter schema ships with the sensor in English and names fields the
 * way the script and the profile file know them: `--domain-suffix-match`,
 * `DOMAIN_SUFFIX_MATCH`. Shown as they are, the German interface labelled its
 * forms in capitals and explained them in English. The words a person reads
 * live in the locale files instead, under `sensorFields.<sensor>.<field>`,
 * where the field is the name without dashes, in lower case, with
 * underscores: `--ssid` and `SSID` are one entry, because they are one fact.
 *
 * Shared between the parameter card and the variant dialog - both used to
 * carry byte-identical private copies, which is how the same description
 * ended up rendered three times on one page.
 */

interface NamedField {
  name: string
  label_key?: string
  description?: string
  description_key?: string
}

/** `--domain-suffix-match` and `DOMAIN_SUFFIX_MATCH` -> `domain_suffix_match`. */
export function fieldKey(name: string): string {
  return name.replace(/^-+/, '').toLowerCase().replace(/-/g, '_')
}

function localKey(sensor: string | undefined, field: string, leaf: string) {
  return sensor ? `sensorFields.${sensor}.${fieldKey(field)}.${leaf}` : null
}

/** The label of a field: its translation if there is one, else its own name. */
export function fieldLabel(t: TFunction, field: NamedField, sensor?: string): string {
  const key = localKey(sensor, field.name, 'label')
  if (key && i18n.exists(key)) return t(key)
  return field.label_key ? t(field.label_key, field.name) : field.name
}

/**
 * The help of a field.
 *
 * The translated text wins; the English plain text from the schema - kept in
 * step with the script's own argparse help by tests/sensor-checks.py - is the
 * fallback for a field the locale files do not know yet.
 */
export function fieldDescription(
  t: TFunction,
  field: Omit<NamedField, 'name'> & { name?: string },
  sensor?: string,
): string {
  const key = field.name ? localKey(sensor, field.name, 'hint') : null
  if (key && i18n.exists(key)) return t(key)
  if (field.description_key) return t(field.description_key, field.description ?? '')
  return field.description ?? ''
}

/** What a choice is called on screen: "PEAP/MSCHAPv2" rather than `peap`. */
export function choiceLabel(
  t: TFunction,
  field: { name: string },
  value: string,
  sensor?: string,
): string {
  const key = localKey(sensor, field.name, `choices.${value}`)
  return key && i18n.exists(key) ? t(key) : value
}
