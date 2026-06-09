import { useState } from 'react'
import { Stack, TextField, Dropdown, PrimaryButton, DefaultButton, IDropdownOption } from '@fluentui/react'
import styles from './EmailAssistant.module.css'

const TONE_OPTIONS: IDropdownOption[] = [
  { key: 'warm', text: 'Warm' },
  { key: 'formal', text: 'Formal' },
  { key: 'brief', text: 'Brief' },
]

export const EmailAssistant = () => {
  const [details, setDetails] = useState<string>('')
  const [originalEmail, setOriginalEmail] = useState<string>('')
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
        body: JSON.stringify({
          details: details.trim(),
          tone,
          original_email: originalEmail.trim(),
        }),
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
    setOriginalEmail('')
    setResult('')
    setError('')
  }

  return (
    <div className={styles.container}>
      <Stack tokens={{ childrenGap: 20 }}>
        <div>
          <h2 className={styles.title}>Email Assistant</h2>
          <p className={styles.subtitle}>
            Describe what you need to communicate and get a polished draft. Paste the original message below if you're writing a reply.
          </p>
        </div>

        <TextField
          label="Original email (optional — paste here if replying)"
          multiline
          rows={4}
          value={originalEmail}
          onChange={(_, val) => setOriginalEmail(val || '')}
          placeholder="Paste the email you're responding to..."
          disabled={loading}
        />

        <TextField
          label="Details"
          multiline
          rows={4}
          value={details}
          onChange={(_, val) => setDetails(val || '')}
          placeholder="Describe what to communicate — key points, any specific instructions, names, dates, links..."
          disabled={loading}
          required
        />

        <Stack.Item styles={{ root: { maxWidth: 180 } }}>
          <Dropdown
            label="Tone"
            selectedKey={tone}
            options={TONE_OPTIONS}
            onChange={(_, opt) => opt && setTone(opt.key as string)}
          />
        </Stack.Item>

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
