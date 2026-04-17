# MemKraft + LangChain (v{VERSION})

Wrap MemKraft as LangChain tools. Copy-paste and go.

Base dir: `{BASE_DIR}`

```python
from langchain.tools import StructuredTool
from pydantic import BaseModel, Field
from memkraft import MemKraft

mk = MemKraft(base_dir="{BASE_DIR}")


class RememberArgs(BaseModel):
    name: str = Field(..., description="Entity name")
    info: str = Field(..., description="New information to record")
    source: str = Field(default="chat", description="Source attribution")


def _remember(name: str, info: str, source: str = "chat") -> str:
    mk.update(name, info, source=source)
    return f"remembered: {name} <- {info[:60]}"


class SearchArgs(BaseModel):
    query: str = Field(..., description="Search query")
    fuzzy: bool = Field(default=True)


def _search(query: str, fuzzy: bool = True) -> list:
    return mk.search(query, fuzzy=fuzzy)


class RecallArgs(BaseModel):
    name: str = Field(..., description="Entity name")


def _recall(name: str) -> str:
    return mk.brief(name) or f"no memory for {name}"


memkraft_tools = [
    StructuredTool.from_function(
        name="memkraft_remember",
        description="Store new information about a person/org/project.",
        func=_remember,
        args_schema=RememberArgs,
    ),
    StructuredTool.from_function(
        name="memkraft_search",
        description="Hybrid search over stored memory.",
        func=_search,
        args_schema=SearchArgs,
    ),
    StructuredTool.from_function(
        name="memkraft_recall",
        description="Return full dossier for an entity.",
        func=_recall,
        args_schema=RecallArgs,
    ),
]
```

## Usage with an agent

```python
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

llm = ChatOpenAI(model="gpt-4o-mini")
prompt = ChatPromptTemplate.from_messages([
    ("system", "You have persistent memory via MemKraft. Prefer search before answering."),
    ("human", "{input}"),
    ("placeholder", "{agent_scratchpad}"),
])
agent = create_openai_tools_agent(llm, memkraft_tools, prompt)
executor = AgentExecutor(agent=agent, tools=memkraft_tools)
```
