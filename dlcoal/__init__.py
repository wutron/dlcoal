"""

   Code for the DLCoal model
   (duplications, losses, and coalescence)

"""

from __future__ import division

# python libs
import copy
import os
import sys
import random
from itertools import chain, izip
from math import *


from dlcoal.ctypes_export import *

# import spidir C lib
dlcoalc = load_library(["..", "lib"], "libdlcoal.so")


# add pre-bundled dependencies to the python path,
# if they are not available already
try:
    import rasmus, compbio, spidir
except ImportError:
    from . import dep
    dep.load_deps()
    import rasmus, compbio


# rasmus libs
from rasmus import util, treelib

# compbio libs
from compbio import birthdeath
from compbio import phylo


# dlcoal libs
from . import coal, duploss



#=============================================================================
# reconciliation

def dlcoal_recon(tree, stree, gene2species,
                 n, duprate, lossrate,
                 pretime=None, premean=None,
                 nsearch=1000,
                 maxdoom=20, nsamples=100,
                 search=None,
                 log=sys.stdout):
    """
    Perform reconciliation using the DLCoal model

    Returns maxrecon is defined as

    maxrecon = {'coal_recon': coal_recon,
                'locus_tree': locus_tree,
                'locus_recon': locus_recon,
                'locus_events': locus_events,
                'daughters': daughters,
                'data': extra_information }
    
    """

    if search is None:
        search = DLCoalTreeSearch

    reconer = DLCoalRecon(tree, stree, gene2species,
                          n, duprate, lossrate,
                          pretime=pretime, premean=premean,
                          maxdoom=maxdoom, nsamples=nsamples, log=log)
    reconer.set_proposer(DLCoalReconProposer(
        tree, stree, gene2species, search=search))
    return reconer.recon(nsearch).get_dict()



class DLCoalRecon (object):

    def __init__(self, tree, stree, gene2species,
                 n, duprate, lossrate,
                 pretime=None, premean=None,
                 maxdoom=20, nsamples=100,
                 name_internal="n", log=sys.stdout):

        # init coal tree
        self.coal_tree = tree
        self.stree = stree
        self.gene2species = gene2species
        self.n = n
        self.duprate = duprate
        self.lossrate = lossrate
        self.pretime = pretime
        self.premean = premean
        self.maxdoom = maxdoom
        self.nsamples = nsamples
        self.name_internal = name_internal
        self.log_stream = log

        self.proposer = DLCoalReconProposer(tree, stree, gene2species)


    def set_proposer(self, proposer):
        """Set the proposal algorithm"""
        self.proposer = proposer


    def set_log(self, log):
        self.log_stream = log
        

    def recon(self, nsearch=1000):
        """Perform reconciliation"""
        
        self.init_search()
        proposal = self.proposer.init_proposal()
        self.maxrecon = proposal.copy()
        for i in xrange(nsearch):
            if i%10 == 0:
                print "search", i
            p = self.eval_proposal(proposal)
            self.eval_search(p, proposal)
            proposal = self.proposer.next_proposal()
        
        # rename locus tree nodes
        rename_nodes(self.maxrecon.locus_tree, self.name_internal)
        
        return self.maxrecon


    def init_search(self):
        """Initialize new search"""

        # init locus tree as congruent to coal tree
        # equivalent to assuming no ILS
        self.proposer.set_locus_tree(self.coal_tree.copy())

        self.maxp = - util.INF
        self.maxrecon = None


    def next_proposal(self):
        """Returns next proposal"""
        self.proposal.next_proposal()


    def eval_proposal(self, proposal):
        """Compute probability of proposal"""

        #util.tic("eval")
        # compute recon probability
        info = {}
        p = prob_dlcoal_recon_topology(self.coal_tree,
                                       proposal.coal_recon,
                                       proposal.locus_tree,
                                       proposal.locus_recon,
                                       proposal.locus_events,
                                       proposal.daughters,
                                       self.stree, self.n,
                                       self.duprate, self.lossrate,
                                       self.pretime, self.premean,
                                       maxdoom=self.maxdoom,
                                       nsamples=self.nsamples,
                                       add_spec=False,
                                       info=info)
        proposal.data = info
        #util.toc()
        
        return p


    def eval_search(self, p, proposal):
        """Evaluate a proposal for search"""
        
        self.log_proposal(proposal)

        if p > self.maxp:
            self.maxp = p
            self.maxrecon = proposal.copy()

            # search with a new copy
            self.proposer.accept()
        else:
            self.proposer.reject()


    def log_proposal(self, proposal):
        self.log_stream.write(repr(proposal) + "\n")
        self.log_stream.flush()



class DLCoalReconProposer (object):

    def __init__(self, coal_tree, stree, gene2species,
                 search=phylo.TreeSearchNni,
                 num_coal_recons=5):
        self._coal_tree = coal_tree
        self._stree = stree
        self._gene2species = gene2species
        self._locus_search = search(None)

        # coal recon search
        self._num_coal_recons = num_coal_recons
        self._i_coal_recons = 0
        self._coal_recon_enum = None
        self._coal_recon_depth = 2
        self._accept_locus = False

        self._recon = None
        

    def set_locus_tree(self, locus_tree):
        self._locus_search.set_tree(locus_tree)

    def init_proposal(self):
        """Get first proposal"""

        if self._locus_search.get_tree() is None:
            self._locus_search.set_tree(self._coal_tree.copy())
        self._i_coal_recons = 0
        self._recon = self._recon_lca(self._locus_search.get_tree().copy())
        
        return self._recon


    def next_proposal(self):
        
        if self._i_coal_recons >= self._num_coal_recons:
            # propose new locus_tree
            
            # if locus_tree has not yet been accepted, then revert it
            if not self._accept_locus:
                self._locus_search.revert()
                
            self._locus_search.propose()
            self._accept_locus = False
            self._i_coal_recons = 0
            locus_tree = self._locus_search.get_tree().copy()
            
            # TODO: make recon root optional
            phylo.recon_root(locus_tree, self._stree,
                             self._gene2species,
                             newCopy=False)
            rename_nodes(locus_tree)

            # propose remaining parts of dlcoal recon
            self._recon = self._recon_lca(locus_tree)
        else:
            # modify coal_recon
            
            try:
                self._i_coal_recons += 1
                self._coal_recon_enum.next()
            except StopIteration:
                self._i_coal_recon = self._num_coal_recons
                return self.next_proposal()

        return self._recon


    def _recon_lca(self, locus_tree):
        # get locus tree, and LCA locus_recon
        locus_recon = phylo.reconcile(locus_tree, self._stree,
                                      self._gene2species)
        locus_events = phylo.label_events(locus_tree, locus_recon)

        # propose daughters (TODO)
        daughters = set()

        # propose LCA coal_recon
        coal_recon = phylo.reconcile(self._coal_tree,
                                     locus_tree, lambda x: x)

        self._coal_recon_enum = phylo.enum_recon(
            self._coal_tree, locus_tree,
            recon=coal_recon,
            depth=self._coal_recon_depth)


        return Recon(coal_recon, locus_tree, locus_recon, locus_events,
                     daughters)


    def accept(self):
        self._accept_locus = True
    

    def reject(self):
        pass


class Recon (object):
    """
    The reconciliation datastructure for the DLCoal model
    """
    
    def __init__(self, coal_recon, locus_tree, locus_recon, locus_events,
                 daughters, data=None):
        self.coal_recon = coal_recon
        self.locus_tree = locus_tree
        self.locus_recon = locus_recon
        self.locus_events = locus_events
        self.daughters = daughters

        if data is None:
            self.data = {}
        else:
            self.data = data

    def copy(self):
        return Recon(self.coal_recon,
                     self.locus_tree, self.locus_recon, self.locus_events,
                     self.daughters, data=copy.deepcopy(self.data))


    def get_dict(self):
        return {"coal_recon": self.coal_recon,
                "locus_tree": self.locus_tree,
                "locus_recon": self.locus_recon,
                "locus_events": self.locus_events,
                "daughters": self.daughters}
    
    
    def __repr__(self):
        return repr({"coal_recon": [(x.name, y.name) for x,y in
                                    self.coal_recon.iteritems()],
                     "locus_tree": self.locus_tree.get_one_line_newick(
                         root_data=True),
                     "locus_top": phylo.hash_tree(self.locus_tree),
                     "locus_recon": [(x.name, y.name) for x,y in
                                     self.locus_recon.iteritems()],
                     "locus_events": [(x.name, y) for x,y in
                                      self.locus_events.iteritems()],
                     "daughters": [x.name for x in self.daughters],
                     "data": self.data})
    


#=============================================================================
# probability functions for DLCoal model

def prob_dlcoal_recon_topology(coal_tree, coal_recon,
                               locus_tree, locus_recon, locus_events,
                               daughters,
                               stree, n, duprate, lossrate,
                               pretime=None, premean=None,
                               maxdoom=20, nsamples=100,
                               add_spec=True, info=None):
    """
    Probability of a reconcile gene tree in the DLCoal model.

    coal_tree    -- coalescent tree
    coal_recon   -- reconciliation of coalescent tree to locus tree
    locus_tree   -- locus tree (has dup-loss)
    locus_recon  -- reconciliation of locus tree to species tree
    locus_events -- events dict for locus tree
    stree        -- species tree
    n            -- population sizes in species tree
    duprate      -- duplication rate
    lossrate     -- loss rate

    You must also specify one of the following
    pretime      -- starting time before species tree
    premean      -- mean starting time before species tree

    """

    
    # init popsizes for locus tree
    stree_popsizes = coal.init_popsizes(stree, n)
    popsizes = {}
    for node in locus_tree:
        popsizes[node.name] = stree_popsizes[locus_recon[node].name]
    
    
    # duploss probability
    try:
        dl_prob = spidir.calc_birth_death_prior(
            locus_tree, stree, locus_recon,
            duprate, lossrate,
            maxdoom=maxdoom)
        
    except Exception, e:
        if "dlcoal_python_fallback" not in globals():
            print >>sys.stderr, e
            print >>sys.stderr, "warning: using python code instead of native"
            globals()["dlcoal_python_fallback"] = 1
            # spidir libs
            from spidir import topology_prior
            
        dl_prob = topology_prior.dup_loss_topology_prior(
            locus_tree, stree, locus_recon, duprate, lossrate,
            maxdoom=maxdoom, events=locus_events)
    
    # daughters probability
    dups = phylo.count_dup(locus_tree, locus_events)
    d_prob = dups * log(.5)


    # integrate over duplication times using sampling
    prob = 0.0
    for i in xrange(nsamples):
        # sample duplication times
        locus_times = duploss.sample_dup_times(
            locus_tree, stree, locus_recon, duprate, lossrate, pretime,
            premean,
            events=locus_events)
        treelib.set_dists_from_timestamps(locus_tree, locus_times)

        # coal topology probability
        coal_prob = prob_locus_coal_recon_topology(
            coal_tree, coal_recon, locus_tree, popsizes, daughters)
        
        prob += exp(coal_prob)
    
    # logging info
    if info is not None:
        info["duploss_prob"] = dl_prob
        info["daughters_prob"] = d_prob
        info["coal_prob"] = util.safelog(prob / nsamples)
        info["prob"] = dl_prob + d_prob + util.safelog(prob / nsamples)
    
    return dl_prob + d_prob + util.safelog(prob / nsamples)


def prob_locus_coal_recon_topology(tree, recon, locus_tree, n, daughters):
    """
    Returns the log probability of a reconciled gene tree ('tree', 'recon')
    from the coalescent model given a locus tree 'locus_tree',
    population sizes 'n', and daughters set 'daughters'
    """

    # initialize popsizes, lineage counts, and divergence times
    popsizes = coal.init_popsizes(locus_tree, n)
    lineages = coal.count_lineages_per_branch(tree, recon, locus_tree)
    locus_times = treelib.get_tree_timestamps(locus_tree)


    # calc log probability
    lnp = coal.prob_multicoal_recon_topology(
        tree, recon, locus_tree, popsizes, lineages=lineages)
    

    def walk(node, gene_counts, leaves):
        if node.is_leaf():
            gene_counts[node.name] = lineages[node][0]
            leaves.add(node)
        else:
            for child in node.children:
                if child in daughters:
                    gene_counts[child.name] = 1
                    leaves.add(child)
                else:
                    walk(child, gene_counts, leaves)

    for daughter in daughters:
        # determine leaves of the coal subtree
        gene_counts = {}
        leaves = set()
        walk(daughter, gene_counts, leaves)

        p = coal.cdf_mrca_bounded_multicoal(
            gene_counts, locus_times[daughter.parent], locus_tree, popsizes,
            sroot=daughter, sleaves=leaves, stimes=locus_times)

        if p == -util.INF:
            return -util.INF

        lnp -= p
    
    return lnp



#=============================================================================
# sampling from the DLCoal model

def sample_dlcoal(stree, n, duprate, lossrate, namefunc=lambda x: x,
                  remove_single=True, name_internal="n",
                  minsize=0, reject=False):
    """Sample a gene tree from the DLCoal model"""

    # generate the locus tree
    while True:
        # TODO: does this take a namefunc?
        locus_tree, locus_recon, locus_events = \
                    birthdeath.sample_birth_death_gene_tree(
            stree, duprate, lossrate)
        if len(locus_tree.leaves()) >= minsize:
            break

    if len(locus_tree.nodes) <= 1:
        # total extinction
        coal_tree = treelib.Tree()
        coal_tree.make_root()
        coal_recon = {coal_tree.root: locus_tree.root}
        daughters = set()
    else:
        # simulate coalescence
        
        # choose daughter duplications
        daughters = set()
        for node in locus_tree:
            if locus_events[node] == "dup":
                daughters.add(node.children[random.randint(0, 1)])

        if reject:
            # use slow rejection sampling (for testing)
            coal_tree, coal_recon = sample_locus_coal_tree_reject(
                locus_tree, n, daughters=daughters, namefunc=namefunc)
        else:
            coal_tree, coal_recon = sample_locus_coal_tree(
                locus_tree, n, daughters=daughters, namefunc=namefunc)

        # clean up coal tree
        if remove_single:
            treelib.remove_single_children(coal_tree)
            phylo.subset_recon(coal_tree, coal_recon)

    if name_internal:
        rename_nodes(coal_tree, name_internal)
        rename_nodes(locus_tree, name_internal)

    # store extra information
    extra = {"locus_tree": locus_tree,
             "locus_recon": locus_recon,
             "locus_events": locus_events,
             "coal_tree": coal_tree,
             "coal_recon": coal_recon,
             "daughters": daughters}

    return coal_tree, extra


def rename_nodes(tree, prefix="n"):
    """Rename nodes that all names are strings"""
    for node in list(tree.postorder()):
        if isinstance(node.name, int):
            name2 = prefix + str(node.name)
            while name2 in tree.nodes:
                name2 = prefix + str(tree.new_name())
            tree.rename(node.name, name2)


def sample_locus_coal_tree(stree, n, leaf_counts=None,
                           daughters=set(),
                           namefunc=None):
    """
    Returns a gene tree from a locus coalescence process
    n -- population size (int or dict)
         If n is a dict it must map from species name to population size
    """
    
    # initialize vector for how many genes per extant species
    if leaf_counts is None:
        leaf_counts = dict((l, 1) for l in stree.leaf_names())

    # initialize function for generating new gene names
    if namefunc is None:
        spcounts = dict((l, 1) for l in stree.leaf_names())
        def namefunc(sp):
            name = sp + "_" + str(spcounts[sp])
            spcounts[sp] += 1
            return name

    stimes = treelib.get_tree_timestamps(stree)

    # initialize population sizes
    popsizes = coal.init_popsizes(stree, n)

    # init gene counts
    counts = dict((n.name, 0) for n in stree)
    counts.update(leaf_counts)

    # init lineage counts
    lineages = {stree.root: [None, None]}
    for node in stree.leaves():
        lineages[node] = [leaf_counts[node.name], None]
    for node in daughters:
        if node not in lineages:
            lineages[node] = [None, 1]
        else:
            lineages[node][1] = 1
        

    def get_subtree(node, leaves, leaf_counts2):
        """collects info of subtree rooted at node"""
        if node.is_leaf():
            leaves.add(node)
            leaf_counts2[node.name] = leaf_counts[node.name]
        else:
            for child in node.children:
                if child in daughters:
                    leaves.add(child)
                    leaf_counts2[child.name] = 1
                else:
                    get_subtree(child, leaves, leaf_counts2)

    # loop through subtrees
    for snode in chain(daughters, [stree.root]):
        # determine leaves of the coal subtree
        leaves = set()
        leaf_counts2 = {}
        get_subtree(snode, leaves, leaf_counts2)
        
        if snode.parent:
            T  = stimes[snode.parent]
        else:
            T = None

        # calc table
        prob_counts = coal.calc_prob_counts_table(
            leaf_counts2, T, stree, popsizes,
            sroot=snode, sleaves=leaves, stimes=stimes)
        
        # sample lineage counts
        try:
            coal.sample_lineage_counts(snode, leaves, popsizes, stimes, T,
                                       lineages, prob_counts)
        except:
            print snode.name
            treelib.draw_tree_names(stree, maxlen=8)
            util.print_dict(lineages, key=lambda x: x.name)
            raise


    # sample coal times
    tree, recon = coal.coal_cond_lineage_counts(
        lineages, stree.root, set(stree.leaves()),
        popsizes, stimes, None, namefunc)
    
    return tree, recon


def sample_locus_coal_tree_reject(locus_tree, n, leaf_counts=None,
                                  daughters=set(),
                                  namefunc=None):
    
    # use rejection sampling
    while True:
        coal_tree, coal_recon = coal.sample_multicoal_tree(
            locus_tree, n, namefunc=lambda x: x)
        lineages = coal.count_lineages_per_branch(
            coal_tree, coal_recon, locus_tree)
        for daughter in daughters:
            if lineages[daughter][1] != 1:
                break
        else:
            break

    return coal_tree, coal_recon



#=============================================================================
# tree search

class DLCoalTreeSearch (phylo.TreeSearch):

    def __init__(self, tree, tree_hash=None):
        phylo.TreeSearch.__init__(self, tree)
        self.search = UniqueTreeSearch(tree, phylo.TreeSearchNni(tree),
                                       tree_hash)

    def set_tree(self, tree):
        self.tree = tree
        self.search.set_tree(tree)

    def reset(self):
        self.search.reset()

    def propose(self):
        self.tree = self.search.propose()
        #util.logger(self.search.search.node1, self.search.search.node2,
        #            self.search.search.child) 
        return self.tree
        
    def revert(self):
        self.tree = self.search.revert()
        return self.tree



class UniqueTreeSearch (phylo.TreeSearch):

    def __init__(self, tree, search, tree_hash=None, maxtries=5):
        phylo.TreeSearch.__init__(self, tree)
        self.search = search
        self.seen = set()
        self._tree_hash = tree_hash if tree_hash else phylo.hash_tree
        self.maxtries = maxtries

    def set_tree(self, tree):
        self.tree = tree
        self.search.set_tree(tree)

    def reset(self):
        self.seen.clear()
        self.search.reset()
        

    def propose(self):

        for i in xrange(self.maxtries):
            if i > 0:
                self.search.revert()
            tree = self.search.propose()
            top = self._tree_hash(tree)
            if top not in self.seen:
                #util.logger("tried", i, len(self.seen))
                break
        else:
            pass
            #util.logger("maxtries", len(self.seen))

        self.seen.add(top)
        self.tree = tree
        return self.tree
        

    def revert(self):
        self.tree = self.search.revert()
        return self.tree



#=============================================================================
# Input/Output


def dlcoal_sims(outdir, nsims, stree, n, duprate, lossrate,
                start=0,
                **options):
    
    for i in xrange(start, nsims):
        outfile = phylo.phylofile(outdir, str(i), "")
        util.makedirs(os.path.dirname(outfile))
        print "simulating", outfile

        # sample a new tree from DLCoal model
        coal_tree, ex = sample_dlcoal(stree, n, duprate, lossrate, **options)
        write_dlcoal_recon(outfile, coal_tree, ex)










def write_dlcoal_recon(filename, coal_tree, extra,
                       exts={"coal_tree": ".coal.tree",
                             "coal_recon": ".coal.recon",
                             "locus_tree": ".locus.tree",
                             "locus_recon": ".locus.recon",
                             "daughters": ".daughters"
                             },
                       filenames={}):
    """Writes a reconciled gene tree to files"""

    # coal
    coal_tree.write(filenames.get("coal_tree", filename + exts["coal_tree"]),
                    rootData=True)
    phylo.write_recon_events(
        filenames.get("coal_recon", filename + exts["coal_recon"]),
        extra["coal_recon"], noevent="none")

    # locus
    extra["locus_tree"].write(
        filenames.get("locus_tree", filename + exts["locus_tree"]),
        rootData=True)
    phylo.write_recon_events(
        filenames.get("locus_recon", filename + exts["locus_recon"]),
        extra["locus_recon"], extra["locus_events"])

    util.write_list(
        filenames.get("daughters", filename + exts["daughters"]),
        [x.name for x in extra["daughters"]])



def read_dlcoal_recon(filename, stree,
                      exts={"coal_tree": ".coal.tree",
                            "coal_recon": ".coal.recon",
                            "locus_tree": ".locus.tree",
                            "locus_recon": ".locus.recon",
                            "daughters": ".daughters"
                            },
                      filenames={}):
    """Reads a reconciled gene tree from files"""

    extra = {}

    # trees
    coal_tree = treelib.read_tree(
        filenames.get("coal_tree", filename + exts["coal_tree"]))
    extra["locus_tree"] = treelib.read_tree(
        filenames.get("locus_tree", filename + exts["locus_tree"]))

    # recons
    extra["coal_recon"], junk = phylo.read_recon_events(
        filenames.get("coal_recon", filename + exts["coal_recon"]),
        coal_tree, extra["locus_tree"])
    extra["locus_recon"], extra["locus_events"] = phylo.read_recon(
        filenames.get("locus_recon", filename + exts["locus_recon"]),
        extra["locus_tree"], stree)


    extra["daughters"] = set(
        extra["locus_tree"].nodes[x] for x in util.read_strings(
        filenames.get("daughters", filename + exts["daughters"])))

    return coal_tree, extra


def read_log(filename):
    """Reads a DLCoal log"""
    stream = util.open_stream(filename)
    for line in stream:
        yield eval(line)
  

def read_log_all(filename):
    """Reads a DLCoal log"""
    stream = util.open_stream(filename)
    return map(eval, stream)
    


## added 30 July 2010
##  should mimic dlcoal_sims, but using the new simulator
## TODO: fix this up when code is more integrated
def dlcoal_sims2(outdir, nsims, stree, n, duprate, lossrate, freq=1.0,
                freqdup=.05, freqloss=.05, forcetime=1e6, start=0, minsize=0,
                **options):
    
    import sim_v2_2 # TODO: remove this when code is more integrated
    
    for i in xrange(start, nsims+start): # changed nsims to nsims+start
        outfile = phylo.phylofile(outdir, str(i), "")
        util.makedirs(os.path.dirname(outfile))
        print "simulating", outfile
 
        # sample a new tree from DLCoal model using new simulator
        coal_tree, ex = sim_v2_2.sample_dlcoal_no_ifix(stree, n, freq, \
                            duprate, lossrate, freqdup, freqloss, forcetime, \
                            minsize=minsize, **options)
        write_dlcoal_recon(outfile, coal_tree, ex)
 



#=============================================================================
# OLD


def prob_dlcoal_recon_topology_old(coal_tree, coal_recon,
                                   locus_tree, locus_recon, locus_events,
                                   daughters,
                                   stree, n, duprate, lossrate,
                                   pretime=None, premean=None,
                                   maxdoom=20, nsamples=100,
                                   add_spec=True):
    """
    Probability of a reconcile gene tree in the DLCoal model.

    coal_tree    -- coalescent tree
    coal_recon   -- reconciliation of coalescent tree to locus tree
    locus_tree   -- locus tree (has dup-loss)
    locus_recon  -- reconciliation of locus tree to species tree
    locus_events -- events dict for locus tree
    stree        -- species tree
    n            -- population sizes in species tree
    duprate      -- duplication rate
    lossrate     -- loss rate

    You must also specify one of the following
    pretime      -- starting time before species tree
    premean      -- mean starting time before species tree

    Note: locus tree must have implied speciation nodes present
    """
    
    dups = phylo.count_dup(locus_tree, locus_events)
    
    # ensure implicit speciations are present
    if add_spec:
        phylo.add_implied_spec_nodes(locus_tree, stree,
                                     locus_recon, locus_events)
    
    # init popsizes for locus tree
    stree_popsizes = coal.init_popsizes(stree, n)
    popsizes = {}
    for node in locus_tree:
        popsizes[node.name] = stree_popsizes[locus_recon[node].name]


    # duploss probability
    dl_prob = spidir.calc_birth_death_prior(locus_tree, stree, locus_recon,
                                            duprate, lossrate,
                                            maxdoom=maxdoom)
    
    # daughters probability
    d_prob = dups * log(.5)


    # integrate over duplication times using sampling
    prob = 0.0
    for i in xrange(nsamples):
        # sample duplication times

        locus_times = duploss.sample_dup_times(
            locus_tree, stree, locus_recon, duprate, lossrate, pretime,
            premean,
            events=locus_events)
        assert len(locus_times) == len(locus_tree.nodes), (
            len(locus_times), len(locus_tree.nodes))
        treelib.set_dists_from_timestamps(locus_tree, locus_times)

        # coal topology probability
        coal_prob = prob_multicoal_recon_topology_old(
            coal_tree, coal_recon, locus_tree, popsizes, daughters)
        
        prob += exp(coal_prob)


    if add_spec:
        removed = treelib.remove_single_children(locus_tree)
        for r in removed:
            del locus_recon[r]
            del locus_events[r]
    
    return dl_prob + d_prob + util.safelog(prob / nsamples)


def prob_multicoal_recon_topology_old(tree, recon, locus_tree, n, daughters):
    """
    Returns the log probability of a reconciled gene tree ('tree', 'recon')
    from the coalescent model given a locus_tree 'locus_tree',
    population sizes 'n', and daughters set 'daughters'
    """

    # init population sizes and lineage counts
    popsizes = coal.init_popsizes(locus_tree, n)
    lineages = coal.count_lineages_per_branch(tree, recon, locus_tree)

    # log probability
    lnp = 0.0
    
    # iterate through species tree branches
    for snode in locus_tree.postorder():
        if snode.parent:
            # non root branch
            a, b = lineages[snode]

            if snode not in daughters:                
                lnp += util.safelog(coal.prob_coal_counts(a, b, snode.dist,
                                          popsizes[snode.name]))
            else:
                assert b == 1
                # the probability of this subtree 1 since complete
                # coalescence is required.

            lnp -= log(coal.num_labeled_histories(a, b))
        else:
            # normal coalesent
            a, b = lineages[snode]
            assert b == 1
            lnp -= log(coal.num_labeled_histories(a, b))

    
    # correct for topologies H(T)
    # find connected subtrees that are in the same species branch
    subtrees = []
    subtree_root = {}
    for node in tree.preorder():
        if node.parent and recon[node] == recon[node.parent]:
            subtree_root[node] = subtree_root[node.parent]
        else:
            subtrees.append(node)
            subtree_root[node] = node

    # find leaves through recursion
    def walk(node, subtree, leaves):
        if node.is_leaf():
            leaves.append(node)
        elif (subtree_root[node.children[0]] != subtree and
              subtree_root[node.children[1]] != subtree):
            leaves.append(node)
        else:
            for child in node.children:
                walk(child, subtree, leaves)

    # apply correction for each subtree
    for subtree in subtrees:
        leaves = []
        for child in subtree.children:
            walk(subtree, subtree, leaves)
        if len(leaves) > 2:
            lnp += log(birthdeath.num_topology_histories(subtree, leaves))

    return lnp


def sample_locus_coal_tree2(stree, n, leaf_counts=None,
                           daughters=set(),
                           namefunc=None):
    """
    Returns a gene tree from a locus coalescence process
    n -- population size (int or dict)
         If n is a dict it must map from species name to population size
    """
    
    # initialize vector for how many genes per extant species
    if leaf_counts is None:
        leaf_counts = dict((l, 1) for l in stree.leaf_names())

    # initialize function for generating new gene names
    if namefunc is None:
        spcounts = dict((l, 1) for l in stree.leaf_names())
        def namefunc(sp):
            name = sp + "_" + str(spcounts[sp])
            spcounts[sp] += 1
            return name

    stimes = treelib.get_tree_timestamps(stree)

    # initialize population sizes
    popsizes = coal.init_popsizes(stree, n)

    # init gene counts
    counts = dict((n.name, 0) for n in stree)
    counts.update(leaf_counts)

    # init reconciliation, events
    recon = {}
    subtrees = {}

    print "locus"
    treelib.draw_tree_names(stree, maxlen=8)

    
    def walk(node, leaves, stubs):
        if node.is_leaf():
            leaves.append(node)
        else:
            for child in node.children:
                if child in daughters:
                    leaves.append(child)
                    stubs.append(child)
                else:
                    walk(child, leaves, stubs)

    for snode in chain(daughters, [stree.root]):
        # determine leaves of the coal subtree
        leaves = []
        sstubs = []
        walk(snode, leaves, sstubs)

        leaf_counts2 = {}
        for leaf in leaves:
            if leaf.name in leaf_counts:
                # leaf species node
                leaf_counts2[leaf.name] = leaf_counts[leaf.name]
            else:
                # daughter node
                leaf_counts2[leaf.name] = 1

        if snode.parent:
            subtree, subrecon = coal.sample_bounded_multicoal_tree(
                stree, popsizes, stimes[snode.parent],
                leaf_counts=leaf_counts2,
                namefunc=namefunc, sleaves=leaves,
                sroot=snode)
            
        else:
            subtree, subrecon = coal.sample_multicoal_tree(
                stree, popsizes, leaf_counts=leaf_counts2, sroot=stree.root,
                sleaves=leaves,
                namefunc=namefunc)

        # determine stubs
        stubs = []
        for node in subtree:
            if node.is_leaf() and subrecon[node] in sstubs:
                stubs.append((node, subrecon[node]))
        subtrees[snode] = (subtree, stubs)
        recon.update(subrecon)

        # DEBUG
        # give unique leaf names
        #for leaf in subtree.leaves():
        #    if isinstance(leaf.name, basestring):
        #        print "l", leaf.name
        #        #tree.rename(leaf.name, namefunc(recon[leaf].name))

        #nodes = list(subtree.preorder())
        #print "subtree", [x.name for x in nodes]
        #print "stubs", stubs
        #treelib.draw_tree_names(subtree, maxlen=8)



    # stitch subtrees together
    tree = treelib.Tree()

    # add all nodes to total tree
    visited = set()
    for snode, (subtree, stubs) in subtrees.iteritems():
        tree.merge_names(subtree)
        #print "names", tree.nodes.keys()
        #for name in subtree.nodes:
        #    assert name not in visited, name
        #    visited.add(name)

    # stitch leaves of the subtree to children subtree lineages
    for snode, (subtree, stubs) in subtrees.iteritems():
        for leaf, snode in stubs:
            child_subtree = subtrees[snode][0]
            tree.add_child(leaf, child_subtree.root)

    # set root
    tree.root = subtrees[stree.root][0].root

    # name leaves
    #for leaf in tree.leaves():
    #    tree.rename(leaf.name, namefunc(recon[leaf].name))


    # DEBUG
    try:
        #nodes = list(tree.preorder())
        #print util.print_dict(util.hist_dict(x.name for x in tree.postorder()))
        #print util.print_dict(util.hist_dict(x.name for x in nodes))
        #print sorted(util.hist_dict(x.name for x in nodes).items(),
        #             key=lambda x: x[1], reverse=True)[0]

        #print "root", tree.root.name
        #treelib.draw_tree_names(tree, maxlen=8)
        
        treelib.assert_tree(tree)
    except:
        print set(tree.nodes.keys()) - set(x.name for x in tree.postorder())
        print set(x.name for x in tree.postorder()) - set(tree.nodes.keys())
        
        raise
    
    return tree, recon



def sample_locus_coal_tree_old(stree, n, leaf_counts=None,
                               daughters=set(),
                               namefunc=None):
    """
    Returns a gene tree from a locus coalescence process
    n -- population size (int or dict)
         If n is a dict it must map from species name to population size
    """

    # TODO: needs proper sampling from BMC

    # initialize vector for how many genes per extant species
    if leaf_counts is None:
        leaf_counts = dict((l, 1) for l in stree.leaf_names())

    # initialize function for generating new gene names
    if namefunc is None:
        spcounts = dict((l, 1) for l in stree.leaf_names())
        def namefunc(sp):
            name = sp + "_" + str(spcounts[sp])
            spcounts[sp] += 1
            return name

    # initialize population sizes
    popsizes = coal.init_popsizes(stree, n)

    # init gene counts
    counts = dict((n.name, 0) for n in stree)
    counts.update(leaf_counts)

    # init reconciliation, events
    recon = {}

    subtrees = {}

    # loop through species tree
    for snode in stree.postorder():        
        # simulate population for one branch
        k = counts[snode.name]
        if snode in daughters:
            # daughter branch, use bounded coalescent
            subtree = coal.sample_bounded_coal_tree(
                k, popsizes[snode.name], snode.dist, capped=True)
            lineages = set(subtree.root)
        elif snode.parent:            
            # non basal branch
            subtree, lineages = coal.sample_censored_coal_tree(
                k, popsizes[snode.name], snode.dist, capped=True)
        else:
            # basal branch
            subtree = coal.sample_coal_tree(k, popsizes[snode.name])
            lineages = set(subtree.root)
        subtrees[snode] = (subtree, lineages)
        if snode.parent:
            counts[snode.parent.name] += len(lineages)
        for node in subtree:
            recon[node] = snode


    # stitch subtrees together
    tree = treelib.Tree()

    # add all nodes to total tree
    for snode, (subtree, lineages) in subtrees.iteritems():
        tree.merge_names(subtree)
        if snode.parent:
            tree.remove(subtree.root)        
            del recon[subtree.root]
    
    for snode in stree:
        if not snode.is_leaf():
            subtree, lineages = subtrees[snode]

            # get lineages from child subtrees
            lineages2 = chain(*[subtrees[child][1]
                                for child in snode.children])

            # ensure leaves are randomly attached
            leaves = subtree.leaves()
            random.shuffle(leaves)

            # stitch leaves of the subtree to children subtree lineages
            for leaf, lineage in izip(leaves, lineages2):
                tree.add_child(leaf, lineage)


    # set root
    tree.root = subtrees[stree.root][0].root    

    # name leaves
    for leaf in tree.leaves():
        tree.rename(leaf.name, namefunc(recon[leaf].name))
        
    return tree, recon



'''
def dlcoal_recon_old(tree, stree, gene2species,
                 n, duprate, lossrate,
                 pretime=None, premean=None,
                 nsearch=1000,
                 maxdoom=20, nsamples=100,
                 search=phylo.TreeSearchNni):
    """
    Perform reconciliation using the DLCoal model

    Returns (maxp, maxrecon) where 'maxp' is the probability of the
    MAP reconciliation 'maxrecon' which further defined as

    maxrecon = {'coal_recon': coal_recon,
                'locus_tree': locus_tree,
                'locus_recon': locus_recon,
                'locus_events': locus_events,
                'daughters': daughters}
    
    """

    # init coal tree
    coal_tree = tree

    # init locus tree as congruent to coal tree
    # equivalent to assuming no ILS
    locus_tree = coal_tree.copy()

    maxp = - util.INF
    maxrecon = None

    # init search
    locus_search = search(locus_tree)

    for i in xrange(nsearch):       
        # TODO: propose other reconciliations beside LCA
        locus_tree2 = locus_tree.copy()
        phylo.recon_root(locus_tree2, stree, gene2species, newCopy=False)
        locus_recon = phylo.reconcile(locus_tree2, stree, gene2species)
        locus_events = phylo.label_events(locus_tree2, locus_recon)

        # propose daughters (TODO)
        daughters = set()

        # propose coal recon (TODO: propose others beside LCA)
        coal_recon = phylo.reconcile(coal_tree, locus_tree2, lambda x: x)

        # compute recon probability
        phylo.add_implied_spec_nodes(locus_tree2, stree,
                                     locus_recon, locus_events)
        p = prob_dlcoal_recon_topology(coal_tree, coal_recon,
                                       locus_tree2, locus_recon, locus_events,
                                       daughters,
                                       stree, n, duprate, lossrate,
                                       pretime, premean,
                                       maxdoom=maxdoom, nsamples=nsamples,
                                       add_spec=False)
        treelib.remove_single_children(locus_tree2)

        if p > maxp:
            maxp = p
            maxrecon = {"coal_recon": coal_recon,
                        "locus_tree": locus_tree2,
                        "locus_recon": locus_recon,
                        "locus_events": locus_events,
                        "daughters": daughters}
            locus_tree = locus_tree2.copy()
            locus_search.set_tree(locus_tree)
        else:
            locus_search.revert()

        # perform local rearrangement to locus tree
        locus_search.propose()




    return maxp, maxrecon






def write_dlcoal_recon2(filename, coal_tree, extra,
                       exts={"coal_tree": ".coal.tree",
                             "coal_recon": ".coal.recon",
                             "locus_tree": ".locus.tree",
                             "locus_recon": ".locus.recon",
                             "locus_events": ".locus.events",
                             "daughters": ".daughters"
                             },
                       filenames={}):
    """Writes a reconciled gene tree to files"""

    # coal
    coal_tree.write(filenames.get("coal_tree", filename + exts["coal_tree"]),
                    rootData=True)
    phylo.write_recon(
        filenames.get("coal_recon", filename + exts["coal_recon"]),
        extra["coal_recon"])

    # locus
    extra["locus_tree"].write(
        filenames.get("locus_tree", filename + exts["locus_tree"]),
        rootData=True)
    phylo.write_recon(
        filenames.get("locus_recon", filename + exts["locus_recon"]),
        extra["locus_recon"])
    phylo.write_events(
        filenames.get("locus_events", filename + exts["locus_events"]),
        extra["locus_events"])

    util.write_list(
        filenames.get("daughters", filename + exts["daughters"]),
        [x.name for x in extra["daughters"]])



def read_dlcoal_recon2(filename, stree,
                      exts={"coal_tree": ".coal.tree",
                            "coal_recon": ".coal.recon",
                            "locus_tree": ".locus.tree",
                            "locus_recon": ".locus.recon",
                            "locus_events": ".locus.events",
                            "daughters": ".daughters"
                            },
                      filenames={}):
    """Reads a reconciled gene tree from files"""

    extra = {}

    # trees
    coal_tree = treelib.read_tree(
        filenames.get("coal_tree", filename + exts["coal_tree"]))
    extra["locus_tree"] = treelib.read_tree(
        filenames.get("locus_tree", filename + exts["locus_tree"]))

    # recons
    extra["coal_recon"] = phylo.read_recon(
        filenames.get("coal_recon", filename + exts["coal_recon"]),
        coal_tree, extra["locus_tree"])
    extra["locus_recon"] = phylo.read_recon(
        filenames.get("locus_recon", filename + exts["locus_recon"]),
        extra["locus_tree"], stree)
    extra["locus_events"] = phylo.read_events(
        filenames.get("locus_events", filename + exts["locus_events"]),
        extra["locus_tree"])


    extra["daughters"] = set(
        extra["locus_tree"].nodes[x] for x in util.read_strings(
        filenames.get("daughters", filename + exts["daughters"])))

    return coal_tree, extra
    
'''
