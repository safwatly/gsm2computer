package com.gsm2computer.bridge.realtime

import org.junit.Assert.assertEquals
import org.junit.Test

class WsSendBudgetTest {

    @Test
    fun sendsWhileTheQueueIsUnderTheCap() {
        val budget = WsSendBudget(maxQueuedBytes = 1000, wedgeAfterMs = 3000)
        assertEquals(WsSendBudget.Decision.SEND, budget.decide(0, 0))
        assertEquals(WsSendBudget.Decision.SEND, budget.decide(1000, 50))
    }

    @Test
    fun dropsWhileOverCapThenWedges() {
        val budget = WsSendBudget(maxQueuedBytes = 1000, wedgeAfterMs = 3000)
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(1001, 0))
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(5000, 2999))
        assertEquals(WsSendBudget.Decision.WEDGE, budget.decide(5000, 3000))
    }

    @Test
    fun aDrainResetsTheWedgeClock() {
        val budget = WsSendBudget(maxQueuedBytes = 1000, wedgeAfterMs = 3000)
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(5000, 0))
        assertEquals(WsSendBudget.Decision.SEND, budget.decide(0, 2500))
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(5000, 2500))
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(5000, 5499))
        assertEquals(WsSendBudget.Decision.WEDGE, budget.decide(5000, 5500))
    }

    @Test
    fun aPartialDrainWhileStillOverCapResetsTheClock() {
        val budget = WsSendBudget(maxQueuedBytes = 1000, wedgeAfterMs = 3000)
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(5000, 0))
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(4000, 2000))
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(4000, 4999))
        assertEquals(WsSendBudget.Decision.WEDGE, budget.decide(4000, 5000))
    }

    @Test
    fun aQueueThatKeepsGrowingWedges() {
        val budget = WsSendBudget(maxQueuedBytes = 1000, wedgeAfterMs = 3000)
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(2000, 0))
        assertEquals(WsSendBudget.Decision.DROP, budget.decide(3000, 1000))
        assertEquals(WsSendBudget.Decision.WEDGE, budget.decide(4000, 3000))
    }
}
