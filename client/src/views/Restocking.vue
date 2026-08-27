<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div class="card budget-card">
      <div class="card-header">
        <h3 class="card-title">{{ t('restocking.budgetLabel') }}</h3>
        <div class="budget-amount">{{ currencySymbol }}{{ budget.toLocaleString() }}</div>
      </div>
      <input
        type="range"
        class="budget-slider"
        min="0"
        max="50000"
        step="500"
        v-model.number="budget"
      />
      <div class="budget-range-labels">
        <span>{{ currencySymbol }}0</span>
        <span>{{ currencySymbol }}50,000</span>
      </div>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.runningTotal') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ totalSelectedCost.toLocaleString() }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.remainingBudget') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ remainingBudget.toLocaleString() }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.recommendedItems') }}</div>
          <div class="stat-value">{{ affordableCount }} / {{ recommendations.length }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }}</h3>
        </div>
        <div v-if="recommendations.length === 0" class="empty-state">
          {{ t('restocking.noRecommendations') }}
        </div>
        <div v-else class="recommendation-list">
          <div
            v-for="rec in recommendations"
            :key="rec.sku"
            :class="['recommendation-row', { 'out-of-budget': !rec.affordable }]"
          >
            <div class="rec-main">
              <div class="rec-name-row">
                <span class="rec-sku">{{ rec.sku }}</span>
                <span class="rec-name">{{ rec.item_name }}</span>
                <span v-if="!rec.affordable" class="badge warning">{{ t('restocking.outOfBudget') }}</span>
              </div>
              <ul class="rec-reasons">
                <li v-for="(reason, idx) in rec.urgency_reasons" :key="idx">{{ reason }}</li>
              </ul>
            </div>
            <div class="rec-figures">
              <div class="rec-qty">{{ rec.recommended_quantity }} {{ t('restocking.table.quantity') }}</div>
              <div class="rec-cost">{{ currencySymbol }}{{ rec.recommended_cost.toLocaleString() }}</div>
              <div class="rec-lead">{{ rec.lead_time_days }} {{ t('common.days') }}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="place-order-bar">
        <div class="place-order-messages">
          <div v-if="submitError" class="error">{{ submitError }}</div>
          <div v-if="submitSuccess" class="submit-success">
            {{ t('restocking.orderPlaced', { orderNumber: submitSuccess.order_number, days: submitSuccess.delivery_lead_time_days }) }}
            <router-link to="/orders">{{ t('orders.title') }} &rarr;</router-link>
          </div>
        </div>
        <button
          class="place-order-btn"
          :disabled="submitting || affordableCount === 0"
          @click="placeOrder"
        >
          {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, watch, computed } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency } = useI18n()
    const currencySymbol = computed(() => currentCurrency.value === 'JPY' ? '¥' : '$')

    const budget = ref(10000)
    const loading = ref(true)
    const error = ref(null)
    const recommendations = ref([])
    const totalSelectedCost = ref(0)
    const remainingBudget = ref(0)

    const submitting = ref(false)
    const submitError = ref(null)
    const submitSuccess = ref(null)

    const { selectedLocation, selectedCategory, getCurrentFilters } = useFilters()

    const affordableCount = computed(() => recommendations.value.filter(r => r.affordable).length)

    const loadRecommendations = async () => {
      try {
        loading.value = true
        error.value = null
        const filters = getCurrentFilters()
        const result = await api.getRestockRecommendations(budget.value, {
          warehouse: filters.warehouse,
          category: filters.category
        })
        recommendations.value = result.recommendations
        totalSelectedCost.value = result.total_selected_cost
        remainingBudget.value = result.remaining_budget
      } catch (err) {
        error.value = 'Failed to load restock recommendations: ' + err.message
      } finally {
        loading.value = false
      }
    }

    // Debounced so dragging the slider doesn't fire a request per pixel
    let debounceTimer = null
    const scheduleLoad = () => {
      submitSuccess.value = null
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(loadRecommendations, 300)
    }

    watch([budget, selectedLocation, selectedCategory], scheduleLoad)

    const placeOrder = async () => {
      try {
        submitting.value = true
        submitError.value = null
        const filters = getCurrentFilters()
        const order = await api.createRestockOrder({
          budget: budget.value,
          warehouse: filters.warehouse,
          category: filters.category
        })
        submitSuccess.value = order
        await loadRecommendations()
      } catch (err) {
        submitError.value = err.response?.data?.detail || ('Failed to place order: ' + err.message)
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadRecommendations)

    return {
      t,
      currencySymbol,
      budget,
      loading,
      error,
      recommendations,
      totalSelectedCost,
      remainingBudget,
      affordableCount,
      submitting,
      submitError,
      submitSuccess,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-card {
  margin-bottom: 1.5rem;
}

.budget-amount {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-slider {
  width: 100%;
  height: 6px;
  border-radius: 999px;
  background: #e2e8f0;
  appearance: none;
  outline: none;
  cursor: pointer;
  margin: 0.75rem 0 0.5rem;
}

.budget-slider::-webkit-slider-thumb {
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  border: 3px solid white;
  box-shadow: 0 0 0 1px #cbd5e1;
  cursor: pointer;
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  border: 3px solid white;
  box-shadow: 0 0 0 1px #cbd5e1;
  cursor: pointer;
}

.budget-slider:focus {
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

.budget-range-labels {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #94a3b8;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
}

.recommendation-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.recommendation-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1.5rem;
  padding: 1rem;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  transition: all 0.2s ease;
}

.recommendation-row.out-of-budget {
  opacity: 0.55;
  background: #f8fafc;
}

.rec-main {
  flex: 1;
  min-width: 0;
}

.rec-name-row {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  margin-bottom: 0.5rem;
}

.rec-sku {
  font-weight: 700;
  color: #0f172a;
  font-size: 0.875rem;
}

.rec-name {
  color: #475569;
  font-size: 0.875rem;
}

.rec-reasons {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.rec-reasons li {
  font-size: 0.8rem;
  color: #64748b;
  padding-left: 0.875rem;
  position: relative;
}

.rec-reasons li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0.5rem;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #cbd5e1;
}

.rec-figures {
  flex-shrink: 0;
  text-align: right;
  min-width: 110px;
}

.rec-qty {
  font-size: 0.813rem;
  color: #64748b;
}

.rec-cost {
  font-size: 1.125rem;
  font-weight: 700;
  color: #0f172a;
  margin: 0.15rem 0;
}

.rec-lead {
  font-size: 0.75rem;
  color: #94a3b8;
}

.place-order-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1.5rem;
  margin-top: 1.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid #e2e8f0;
}

.place-order-messages {
  flex: 1;
}

.submit-success {
  background: #d1fae5;
  border: 1px solid #6ee7b7;
  color: #065f46;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.875rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.submit-success a {
  color: #065f46;
  font-weight: 700;
  text-decoration: underline;
}

.place-order-btn {
  flex-shrink: 0;
  padding: 0.75rem 1.75rem;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  font-weight: 600;
  font-size: 0.938rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.35);
}

.place-order-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
