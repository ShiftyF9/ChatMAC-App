import { useState } from 'react'
import { Stack, TextField, Dropdown, PrimaryButton, DefaultButton, IDropdownOption } from '@fluentui/react'
import styles from './EmailAssistant.module.css'

const TONE_OPTIONS: IDropdownOption[] = [
  { key: 'warm', text: 'Warm' },
  { key: 'formal', text: 'Formal' },
  { key: 'brief', text: 'Brief' },
]

const PURPOSE_OPTIONS: IDropdownOption[] = [
  { key: 'seminar announcement', text: 'Seminar Announcement' },
  { key: 'event invitation', text: 'Event Invitation' },
  { key: 'schedule change', text: 'Schedule Change' },
  { key: 'member communication', text: 'Member Communication' },
  { key: 'board communication', text: 'Board Communication' },
  { key: 'general announcement', text: 'General Announcement' },
]

export const EmailAssistant = () => {
  const [purpose, setPurpose] = useState<string>('seminar announcement')
  const [details, setDetails] = useState<string>('')
  const [tone, setTone] = useState<string>('warm')
  const [result, setResult] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string>('')
  const [copied, setCopied] = useState<boolean>(false)

  const handleGenerate = async () => {
    if (!details.trim()) return
    setLoading(true)
    setError('')
    setResult('')
    setCopied(false)

    try {
      const res = await fetch('/email/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ purpose, details: details.trim(), tone }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
      setResult(data.text)
    } catch (err: any) {
      setError(err.message || 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  const handleCopy = async () => {
    await navigator.clipboard.writeText(result)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleClear = () => {
    setDetails('')
    setResult('')
    setError('')
  }

  return (
    <div className={styles.container}>
      <Stack tokens={{ childrenGap: 20 }}>
        <div>
          <h2 className={styles.title}>Email Assistant</h2>
          <p className={styles.subtitle}>
            Describe what you need to communicate, and get a polished draft for the dojo community.
          </p>
        </div>

        <Stack horizontal tokens={{ childrenGap: 16 }} wrap>
          <Stack.Item grow={1} styles={{ root: { minWidth: 220 } }}>
            <Dropdown
              label="Type"
              selectedKey={purpose}
              options={PURPOSE_OPTIONS}
              onChange={(_, opt) => opt && setPurpose(opt.key as string)}
            />
          </Stack.Item>
          <Stack.Item grow={1} styles={{ root: { minWidth: 160 } }}>
            <Dropdown
              label="Tone"
              selectedKey={tone}
              options={TONE_OPTIONS}
              onChange={(_, opt) => opt && setTone(opt.key as string)}
            />
          </Stack.Item>
        </Stack>

        <TextField
          label="Key details"
          multiline
          rows={5}
          value={details}
          onChange={(_, val) => setDetails(val || '')}
          placeholder={
            'Describe what to include — instructor name, dates, location, cost, registration link, any specific messaging...'
          }
          disabled={loading}
        />

        <Stack horizontal tokens={{ childrenGap: 8 }}>
          <PrimaryButton
            text={loading ? 'Drafting…' : 'Generate Draft'}
            onClick={handleGenerate}
            disabled={loading || !details.trim()}
          />
          <DefaultButton text="Clear" onClick={handleClear} disabled={loading} />
        </Stack>

        {error && (
          <div className={styles.errorBox}>{error}</div>
        )}

        {result && (
          <div className={styles.resultContainer}>
            <Stack horizontal horizontalAlign="space-between" verticalAlign="center" className={styles.resultHeader}>
              <span className={styles.resultLabel}>Generated Draft</span>
              <DefaultButton
                text={copied ? 'Copied!' : 'Copy'}
                iconProps={{ iconName: copied ? 'CheckMark' : 'Copy' }}
                onClick={handleCopy}
                styles={{ root: { minWidth: 90 } }}
              />
            </Stack>
            <pre className={styles.resultText}>{result}</pre>
          </div>
        )}
      </Stack>
    </div>
  )
}

export default EmailAssistant
