import logging

logger = logging.getLogger("AetherisTracker")

class CostTracker:
    """
    Tracks cost and tokens used across the session.
    """
    def __init__(self):
        self.total_cost = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latest_prompt_tokens = 0
        self.latest_completion_tokens = 0
        
    def add_usage(self, response):
        """
        Extracts usage from a LiteLLM response and updates totals.
        """
        try:
            usage = response.usage
            self.latest_prompt_tokens = getattr(usage, 'prompt_tokens', 0)
            self.latest_completion_tokens = getattr(usage, 'completion_tokens', 0)
            
            self.prompt_tokens += self.latest_prompt_tokens
            self.completion_tokens += self.latest_completion_tokens
            
            # LiteLLM sometimes calculates cost via response._hidden_params["custom_llm_provider"]
            import litellm
            cost = litellm.completion_cost(completion_response=response)
            if cost:
                self.total_cost += cost
                
        except Exception as e:
            logger.debug(f"Could not track cost: {e}")

    def summary(self):
        return {
            "total_cost_usd": self.total_cost,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latest_prompt_tokens": self.latest_prompt_tokens,
            "latest_completion_tokens": self.latest_completion_tokens
        }
