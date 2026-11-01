const { returnToEvents, returnToAssistant } = require('../../utils/navigation.js')

Component({
  properties: {
    source: { type: String, value: '' }
  },
  methods: {
    goEvents() { return this.data.source === 'assistant' ? returnToAssistant() : returnToEvents() }
  }
})
