from collections import defaultdict,deque
class Tree:
    def __init__(self):self.parent={};self.children=defaultdict(set)
    def attach(self,parent,child):
        if parent==child:raise ValueError('self cycle')
        cur=parent
        while cur in self.parent:
            cur=self.parent[cur]
            if cur==child:raise ValueError('cycle')
        if child in self.parent:self.children[self.parent[child]].discard(child)
        self.parent[child]=parent;self.children[parent].add(child)
    def detach(self,child):
        p=self.parent.pop(child,None)
        if p:self.children[p].discard(child)
    def descendants(self,root):
        out=set();q=deque([root])
        while q:
            x=q.popleft()
            for c in self.children[x]:
                if c not in out:out.add(c);q.append(c)
        return out
